#!/usr/bin/env python3
"""Stdlib checks for the racecast dispatcher routing. Run: python3 tests/test_racecast.py"""
import importlib.util, io, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("racecast", os.path.join(ROOT, "src", "racecast.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# The takeover announcement does real network and DB I/O (sheet fetch, Discord,
# health DB), so it is a no-op for the whole file and the suite stays offline. The
# saved original is verified in its own seam-isolated test below. (#317)
_ORIG_ANNOUNCE_TAKEOVER = m._announce_takeover
m._announce_takeover = lambda *a, **k: None


def t_frozen_child_env_moves_the_extraction_dir_out_of_os_temp():
    # A frozen daemon lives for days; the OS reaps its temp dir underneath it.
    # The child must unpack into runtime/bundle instead, and only the parent can
    # decide this, because the bootloader reads TMPDIR before Python.
    with tempfile.TemporaryDirectory() as tmp:
        orig_frozen, orig_base = m.IS_FROZEN, m._runtime_base_dir
        try:
            m.IS_FROZEN = True
            m._runtime_base_dir = lambda: tmp
            env = m._frozen_child_env()
            assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
            expected = os.path.join(tmp, "bundle")
            key = "TEMP" if os.name == "nt" else "TMPDIR"
            assert env[key] == expected, env[key]
            assert os.path.isdir(expected)     # created, or the child would fail
        finally:
            m.IS_FROZEN, m._runtime_base_dir = orig_frozen, orig_base


def t_frozen_child_env_is_none_when_not_frozen():
    assert m._frozen_child_env() is None, "source runs must inherit the environment untouched"


def t_service_start():
    assert m.route(["relay", "start"]) == \
        {"kind": "service", "command": "relay", "verb": "start", "rest": []}


def t_relay_run_forwards_rest():
    r = m.route(["relay", "run", "--no-pov"])
    assert r["kind"] == "service" and r["verb"] == "run" and r["rest"] == ["--no-pov"]


def t_companion_and_streams_verbs():
    assert m.route(["companion", "logs"])["verb"] == "logs"
    assert m.route(["streams", "restart"])["verb"] == "restart"


def t_aggregate_status():
    assert m.route(["status"]) == {"kind": "aggregate"}


def t_oneshot_with_args():
    assert m.route(["cookies", "chrome"]) == \
        {"kind": "oneshot", "command": "cookies", "rest": ["chrome"]}
    assert m.route(["preflight"])["command"] == "preflight"


def t_help_when_empty():
    assert m.route([])["kind"] == "help"


def t_run_only_valid_for_relay():
    _raises(lambda: m.route(["companion", "run"]))


def t_bad_verb_and_unknown_command_raise():
    _raises(lambda: m.route(["relay", "bogus"]))
    _raises(lambda: m.route(["nonsense"]))


def t_relay_open_verbs():
    for verb in ("open-panel", "open-hud", "open-status"):
        assert m.route(["relay", verb])["verb"] == verb


def t_companion_open_verbs():
    for verb in ("open-buttons", "open-admin"):
        assert m.route(["companion", verb])["verb"] == verb


def t_open_verbs_are_not_cross_service():
    _raises(lambda: m.route(["companion", "open-panel"]))
    _raises(lambda: m.route(["relay", "open-buttons"]))
    _raises(lambda: m.route(["streams", "open-hud"]))


def t_tailscale_verbs():
    for verb in ("up", "down", "status"):
        assert m.route(["tailscale", verb]) == \
            {"kind": "service", "command": "tailscale", "verb": verb, "rest": []}


def t_tailscale_bad_verb_raises():
    _raises(lambda: m.route(["tailscale"]))
    _raises(lambda: m.route(["tailscale", "restart"]))


def t_tailscale_login_hint_linux_points_to_cli_not_a_gui_app():
    # Linux has no Tailscale GUI app: the first sign-in is `sudo tailscale up`
    # plus the printed browser URL, NOT "open the app". Whole-string equality, not
    # a substring `in` check, because the latter trips CodeQL's URL-sanitization
    # query.
    hint = m._tailscale_login_hint("linux")
    assert hint == ("run `sudo tailscale up` in a terminal, then open the printed "
                    "https://login.tailscale.com/… URL in a browser to sign in")
    assert "sudo tailscale up" in hint        # the actual command to run (not a URL)
    assert "app" not in hint.lower()          # never tells Linux users to "open the app"


def t_tailscale_login_hint_desktop_uses_the_gui_app():
    for plat in ("darwin", "win32"):
        assert m._tailscale_login_hint(plat) == "open the Tailscale app and sign in"


def t_tailscale_operator_hint_linux_points_to_one_time_operator_fix():
    # `tailscale up/down` need root on Linux ("prefs write access denied"); the
    # durable fix that makes the Control Center buttons work without sudo is the
    # one-time `tailscale set --operator`.
    hint = m._tailscale_operator_hint("down", "linux")
    assert "--operator=$USER" in hint
    assert "sudo tailscale down" in hint        # immediate workaround for this verb
    assert "sudo tailscale up" in m._tailscale_operator_hint("up", "linux")


def t_tailscale_operator_hint_empty_off_linux():
    for plat in ("darwin", "win32"):
        assert m._tailscale_operator_hint("up", plat) == ""


def t_obs_refresh_route():
    assert m.route(["obs", "refresh"]) == \
        {"kind": "service", "command": "obs", "verb": "refresh", "rest": []}


def t_obs_bad_verb_raises():
    _raises(lambda: m.route(["obs"]))
    _raises(lambda: m.route(["obs", "bogus"]))


def t_sheet_routes():
    for verb in ("url", "open"):
        assert m.route(["sheet", verb]) == \
            {"kind": "service", "command": "sheet", "verb": verb, "rest": []}


def t_sheet_bad_verb_raises():
    _raises(lambda: m.route(["sheet"]))
    _raises(lambda: m.route(["sheet", "bogus"]))


def t_sheet_url_cmd_exits_without_sheet_id():
    old = m._active_sheet_url
    m._active_sheet_url = lambda: ""
    try:
        try:
            m.sheet_url_cmd([])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "no SHEET_ID" in str(e.code)
    finally:
        m._active_sheet_url = old


def t_sheet_url_cmd_prints_url(capsys=None):
    old_url, old_open = m._active_sheet_url, m._open_url
    opened = []
    m._active_sheet_url = lambda: "https://docs.google.com/spreadsheets/d/X/edit"
    m._open_url = opened.append
    try:
        m.sheet_open_cmd([])
        assert opened == ["https://docs.google.com/spreadsheets/d/X/edit"]
    finally:
        m._active_sheet_url, m._open_url = old_url, old_open


def t_route_obs_benchmark():
    action = m.route(["obs", "benchmark", "--window", "30"])
    assert action["command"] == "obs" and action["verb"] == "benchmark"
    assert action["rest"] == ["--window", "30"]
    assert m.DISPATCH[("obs", "benchmark")] is m.obs_benchmark_cmd


def t_parse_benchmark_args_defaults_and_flags():
    import obs_benchmark as ob
    import obs_ws
    assert m._parse_benchmark_args([]) == {
        "window_s": ob.DEFAULT_WINDOW_S, "settle_s": ob.DEFAULT_SETTLE_S,
        "scene": obs_ws.STINT_SCENE, "keep_recording": False, "json": False}
    o = m._parse_benchmark_args(["--window", "90", "--settle", "3", "--scene", "Split",
                                 "--keep-recording", "--json"])
    assert o == {"window_s": 90, "settle_s": 3, "scene": "Split",
                 "keep_recording": True, "json": True}


def t_parse_benchmark_args_rejects_bad_input():
    # OBS rebuilds its input after the rejoin; a settle below 3 s samples that (#618)
    for bad in (["--window"], ["--window", "x"], ["--window", "5"], ["--settle", "2"],
                ["--scene"], ["--bogus"]):
        try:
            m._parse_benchmark_args(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


def t_obs_benchmark_cmd_exits_when_relay_down():
    old = m._relay_http_ok
    m._relay_http_ok = lambda: False
    try:
        try:
            m.obs_benchmark_cmd([])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "relay not responding" in str(e.code)
    finally:
        m._relay_http_ok = old


def t_benchmark_relay_raises_when_the_relay_refuses_a_tier():
    old = m._relay_post_json
    sent = []
    m._relay_post_json = lambda url, body, timeout=3: sent.append((url, body)) or {
        "ok": False, "error": "unknown feed"}
    try:
        try:
            m._BenchmarkRelay("http://relay").set_quality("A", "robust")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "unknown feed" in str(exc)
    finally:
        m._relay_post_json = old
    assert sent == [("http://relay/feed/A/quality", {"tier": "robust"})]


def t_benchmark_relay_rejoins_obs_through_the_feed_reset():
    old = m._relay_post_json
    sent = []
    m._relay_post_json = lambda url, body, timeout=3: sent.append((url, body)) or {
        "ok": True, "feed": "A"}
    try:
        m._BenchmarkRelay("http://relay").feed_reset("A")
        m._relay_post_json = lambda url, body, timeout=3: {"ok": False, "error": "no OBS"}
        try:
            m._BenchmarkRelay("http://relay").feed_reset("A")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "no OBS" in str(exc)
    finally:
        m._relay_post_json = old
    assert sent == [("http://relay/obs/feed-reset", {"feed": "A"})]


def t_obs_refresh_cmd_exits_when_relay_down():
    old = m._relay_http_ok
    m._relay_http_ok = lambda: False
    try:
        try:
            m.obs_refresh_cmd([])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "relay not responding" in str(e.code)
    finally:
        m._relay_http_ok = old


def t_http_url():
    assert m._http_url("127.0.0.1", 8088, "/panel") == "http://127.0.0.1:8088/panel"
    assert m._http_url("100.64.10.20", 8000, "/tablet") == "http://100.64.10.20:8000/tablet"


def t_src_base_modes():
    assert m._src_base(False, "", os.path.join("repo", "src")) == os.path.join("repo", "src")
    assert m._src_base(True, os.path.join("tmp", "_MEI1"), "x") == \
        os.path.join("tmp", "_MEI1", "src")


def t_untranslocate_app_translocation():
    # macOS App Translocation only affects a frozen .app launched from Finder, so
    # both guards must short-circuit BEFORE the (macOS-only) resolver is consulted.
    seen = []
    def resolver(p):
        seen.append(p); return "/Users/x/racecast/racecast-ui.app/Contents/MacOS/racecast-ui"
    tl = "/private/var/folders/pk/T/AppTranslocation/UUID/d/racecast-ui.app/Contents/MacOS/racecast-ui"
    assert m._untranslocate(tl, frozen=False, platform="darwin", resolver=resolver) == tl
    assert m._untranslocate(tl, frozen=True, platform="linux", resolver=resolver) == tl
    assert seen == []
    # frozen + macOS: map the translocated path back to its real on-disk location.
    assert m._untranslocate(tl, frozen=True, platform="darwin", resolver=resolver) == \
        "/Users/x/racecast/racecast-ui.app/Contents/MacOS/racecast-ui"
    # best-effort: a resolver that fails (None / raises) falls back to the input.
    assert m._untranslocate(tl, frozen=True, platform="darwin",
                            resolver=lambda p: None) == tl
    def boom(p):
        raise OSError("Security framework unavailable")
    assert m._untranslocate(tl, frozen=True, platform="darwin", resolver=boom) == tl


def t_runtime_base_modes():
    assert m._runtime_base(False, "python3", os.path.join("repo", "src")) == \
        os.path.join("repo", "runtime")
    assert m._runtime_base(False, "python3", "pkg") == os.path.join("pkg", "runtime")
    assert m._runtime_base(True, os.path.join("apps", "racecast"), "ignored") == \
        os.path.join("apps", "runtime")


def t_force_utf8_io_reconfigures_and_is_safe():
    calls = []
    class Stream:
        def reconfigure(self, **kw):
            calls.append(kw)
    class NoReconfigure:                      # py<3.7 / an exotic stream
        pass
    class Raises:
        def reconfigure(self, **kw):
            raise OSError("console detached")
    # None models a --windowed app whose PyInstaller build has no stdout. It must
    # never raise; non-reconfigurable or raising streams are skipped, not fatal.
    m._force_utf8_io([Stream(), NoReconfigure(), Raises(), None, Stream()])
    assert calls == [{"encoding": "utf-8", "errors": "replace"}] * 2


def t_parse_env_text():
    text = "# comment\nRACECAST_SHEET_ID=abc\nOTHER_TIMER_URL='http://x'\n\nnot a pair\n"
    assert m.parse_env_text(text) == {"RACECAST_SHEET_ID": "abc", "OTHER_TIMER_URL": "http://x"}


def t_ensure_env_file_creates_once():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, ".env.example"), "w", encoding="utf-8") as fh:
            fh.write("RACECAST_SHEET_ID=\n")
        # not frozen -> no-op
        assert m.ensure_env_file(d, frozen=False) is False
        assert not os.path.exists(os.path.join(d, ".env"))
        # frozen + template + no .env -> created from the template
        assert m.ensure_env_file(d, frozen=True) is True
        with open(os.path.join(d, ".env"), encoding="utf-8") as fh:
            assert fh.read() == "RACECAST_SHEET_ID=\n"
        # existing .env is never overwritten
        with open(os.path.join(d, ".env"), "w", encoding="utf-8") as fh:
            fh.write("RACECAST_SHEET_ID=real\n")
        assert m.ensure_env_file(d, frozen=True) is False
        with open(os.path.join(d, ".env"), encoding="utf-8") as fh:
            assert fh.read() == "RACECAST_SHEET_ID=real\n"


def t_cleanup_old_binary():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        old = os.path.join(d, "racecast-old.exe")
        with open(old, "wb") as fh:
            fh.write(b"x")
        # only frozen windows cleans up
        assert m.cleanup_old_binary(d, frozen=False, platform="win32") is False
        assert m.cleanup_old_binary(d, frozen=True, platform="darwin") is False
        assert os.path.exists(old)
        assert m.cleanup_old_binary(d, frozen=True, platform="win32") is True
        assert not os.path.exists(old)
        # absent file -> quiet False
        assert m.cleanup_old_binary(d, frozen=True, platform="win32") is False


def t_cleanup_old_binary_also_removes_ui():
    # install_ui renames a locked, running racecast-ui.exe aside to
    # racecast-ui-old.exe during a self-update; cleanup must sweep that too, not
    # only the main binary's racecast-old.exe.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ui_old = os.path.join(d, "racecast-ui-old.exe")
        with open(ui_old, "wb") as fh:
            fh.write(b"x")
        assert m.cleanup_old_binary(d, frozen=True, platform="win32") is True
        assert not os.path.exists(ui_old)


def t_ensure_example_profile_seeds_once():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bundled = os.path.join(d, "bundled-example")
        os.makedirs(bundled)
        with open(os.path.join(bundled, "profile.env"), "w", encoding="utf-8") as fh:
            fh.write("NAME=Example League\nSHEET_ID=\n")
        home = os.path.join(d, "home")
        os.makedirs(home)
        # not frozen -> no-op
        assert m.ensure_example_profile(home, frozen=False, bundled=bundled) is False
        assert not os.path.exists(os.path.join(home, "profiles", "example"))
        # frozen + bundled template + no profiles/example -> seeded next to the binary
        assert m.ensure_example_profile(home, frozen=True, bundled=bundled) is True
        seeded = os.path.join(home, "profiles", "example", "profile.env")
        assert os.path.isfile(seeded)
        # existing profiles/example is never clobbered
        with open(seeded, "w", encoding="utf-8") as fh:
            fh.write("NAME=Edited\n")
        assert m.ensure_example_profile(home, frozen=True, bundled=bundled) is False
        with open(seeded, encoding="utf-8") as fh:
            assert fh.read() == "NAME=Edited\n"


def t_ensure_example_profile_without_bundle():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        os.makedirs(home)
        missing = os.path.join(d, "nope")
        assert m.ensure_example_profile(home, frozen=True, bundled=missing) is False
        assert not os.path.exists(os.path.join(home, "profiles", "example"))


def t_ensure_env_file_without_template():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert m.ensure_env_file(d, frozen=True) is False
        assert not os.path.exists(os.path.join(d, ".env"))


def t_ensure_env_file_copy_failure():
    import tempfile
    def boom(src, dst):
        raise OSError("boom")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, ".env.example"), "w", encoding="utf-8") as fh:
            fh.write("RACECAST_SHEET_ID=\n")
        orig = m.shutil.copyfile
        m.shutil.copyfile = boom
        try:
            assert m.ensure_env_file(d, frozen=True) is False
        finally:
            m.shutil.copyfile = orig
        assert not os.path.exists(os.path.join(d, ".env"))


def t_pick_ca_bundle():
    ex = lambda paths: (lambda p: p in paths)
    # build's own cafile exists -> trust it, no override
    assert m.pick_ca_bundle("/build/cert.pem", None, ("/etc/a",), exists=ex({"/build/cert.pem"})) is None
    # build's capath exists -> trust it
    assert m.pick_ca_bundle(None, "/build/certs", ("/etc/a",), exists=ex({"/build/certs"})) is None
    # neither exists -> first existing candidate
    assert m.pick_ca_bundle("/build/cert.pem", "/build/certs", ("/etc/a", "/etc/b"),
                            exists=ex({"/etc/b"})) == "/etc/b"
    # nothing exists anywhere -> None (leave things alone)
    assert m.pick_ca_bundle(None, None, ("/etc/a",), exists=ex(set())) is None


def t_augment_path():
    ex = lambda paths: (lambda p: p in paths)
    sep = os.pathsep
    # a missing-from-PATH tool dir that exists gets prepended (Homebrew wins)
    assert m.augment_path("/usr/bin", ("/opt/homebrew/bin", "/usr/local/bin"),
                          exists=ex({"/opt/homebrew/bin"})) == "/opt/homebrew/bin" + sep + "/usr/bin"
    # already on PATH -> nothing to add (no duplicate)
    assert m.augment_path("/opt/homebrew/bin" + sep + "/usr/bin",
                          ("/opt/homebrew/bin",), exists=ex({"/opt/homebrew/bin"})) is None
    # candidate doesn't exist on disk -> left alone
    assert m.augment_path("/usr/bin", ("/opt/homebrew/bin",), exists=ex(set())) is None
    # several existing+missing dirs keep candidate order, ahead of the old PATH
    assert m.augment_path("/usr/bin", ("/opt/homebrew/bin", "/usr/local/bin"),
                          exists=ex({"/opt/homebrew/bin", "/usr/local/bin"})) \
        == sep.join(("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"))
    # empty PATH -> just the existing candidates
    assert m.augment_path("", ("/opt/homebrew/bin", "/usr/local/bin"),
                          exists=ex({"/opt/homebrew/bin"})) == "/opt/homebrew/bin"


def t_ensure_tool_path_adds_managed_bin():
    """install-tools drops direct-download tools (deno on Linux, the Ookla
    speedtest CLI on mac/Linux) into runtime/bin, never on the user's shell PATH.
    _ensure_tool_path() must prepend it so preflight and the spawned relay resolve
    them."""
    import tempfile
    td = tempfile.mkdtemp()
    binr = os.path.join(td, "runtime", "bin")
    os.makedirs(binr)
    orig_rt, orig_path = m._runtime_base_dir, os.environ.get("PATH", "")
    m._runtime_base_dir = lambda: os.path.join(td, "runtime")
    try:
        os.environ["PATH"] = os.path.join("/usr", "bin")
        m._ensure_tool_path()
        parts = os.environ["PATH"].split(os.pathsep)
        assert parts[0] == binr            # prepended ahead of the old PATH
    finally:
        m._runtime_base_dir = orig_rt
        os.environ["PATH"] = orig_path


def t_ensure_tool_path_noop_when_bin_absent():
    import tempfile
    td = tempfile.mkdtemp()                # runtime/bin does NOT exist here
    orig_rt, orig_path = m._runtime_base_dir, os.environ.get("PATH", "")
    m._runtime_base_dir = lambda: os.path.join(td, "runtime")
    try:
        os.environ["PATH"] = os.path.join("/usr", "bin")
        m._ensure_tool_path()
        assert os.environ["PATH"] == os.path.join("/usr", "bin")   # unchanged
    finally:
        m._runtime_base_dir = orig_rt
        os.environ["PATH"] = orig_path


def t_script_invocation_repo():
    import sys as _sys
    kind, argv, _ = m._script_invocation("scripts/preflight.py", ["--quick"], False)
    assert kind == "subprocess"
    assert argv[0] == _sys.executable
    assert argv[1].endswith(os.path.join("scripts", "preflight.py"))
    assert argv[-1] == "--quick"


def t_script_invocation_frozen():
    kind, path, args = m._script_invocation("relay/racecast-feeds.py", ["--no-pov"], True,
                                            base=os.path.join("MEI", "src"))
    assert kind == "inprocess"
    assert path == os.path.join("MEI", "src", "relay", "racecast-feeds.py")
    assert args == ["--no-pov"]


def t_relay_daemon_argv():
    import sys as _sys
    repo = m._relay_daemon_argv(["--no-pov"], False)
    assert repo[0] == _sys.executable and repo[1].endswith("racecast-feeds.py")
    assert "--runtime-dir" in repo and repo[-1] == "--no-pov"
    assert m._relay_daemon_argv(["--no-pov"], True) == \
        [_sys.executable, "relay", "run", "--no-pov"]


def t_oneshot_extra():
    # Signature: (command, rest, runtime_dir, base_dir). --out is always injected,
    # profile-scoped, not only when frozen.
    R = os.path.join("x", "runtime", "demo")   # profile runtime
    B = os.path.join("x", "runtime")           # base runtime
    assert m._oneshot_extra("preflight", [], R, B) == ["--runtime-dir", B]
    assert m._oneshot_extra("graphics", [], R, B) == \
        ["--out", os.path.join(R, "graphics")]
    assert m._oneshot_extra("media", [], R, B) == ["--out", os.path.join(R, "media")]
    # setup INJECTS media/graphics dirs into the collection, always profile-scoped.
    assert m._oneshot_extra("setup", [], R, B) == \
        ["--out", os.path.join(R, "GT_Racing_Endurance.import.json"),
         "--media", os.path.join(R, "media"),
         "--graphics", os.path.join(R, "graphics")]
    assert m._oneshot_extra("setup", ["--media", "m"], R, B) == \
        ["--out", os.path.join(R, "GT_Racing_Endurance.import.json"),
         "--graphics", os.path.join(R, "graphics")]
    assert m._oneshot_extra(
        "setup", ["--out", "z", "--media", "m", "--graphics", "g"], R, B) == []
    assert m._oneshot_extra("graphics", ["--out", "z"], R, B) == []

    # --overlay-css is injected for `setup` only when the passed path exists.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        css = os.path.join(d, "hud.css")
        assert m._oneshot_extra("setup", [], R, B, overlay_css=css) == \
            ["--out", os.path.join(R, "GT_Racing_Endurance.import.json"),
             "--media", os.path.join(R, "media"),
             "--graphics", os.path.join(R, "graphics")]            # file absent -> skipped
        with open(css, "w") as fh:
            fh.write("#pov { left: 1516px; }")
        assert m._oneshot_extra("setup", [], R, B, overlay_css=css) == \
            ["--out", os.path.join(R, "GT_Racing_Endurance.import.json"),
             "--media", os.path.join(R, "media"),
             "--graphics", os.path.join(R, "graphics"),
             "--overlay-css", css]                                 # file present -> added
        # explicit --overlay-css in rest wins: the auto one is not appended.
        assert m._oneshot_extra("setup", ["--overlay-css", "x"], R, B,
                                overlay_css=css).count("--overlay-css") == 0

    # --profile-graphics is injected for `setup` only when the committed dir exists.
    with tempfile.TemporaryDirectory() as d:
        pg = os.path.join(d, "graphics")
        assert m._oneshot_extra("setup", [], R, B, profile_graphics=pg).count(
            "--profile-graphics") == 0                                 # dir absent -> skipped
        os.makedirs(pg)
        assert m._oneshot_extra("setup", [], R, B, profile_graphics=pg)[-2:] == \
            ["--profile-graphics", pg]                                 # dir present -> added
        # not injected for non-setup commands, and an explicit one in rest wins.
        assert m._oneshot_extra("graphics", [], R, B, profile_graphics=pg).count(
            "--profile-graphics") == 0
        assert m._oneshot_extra("setup", ["--profile-graphics", "x"], R, B,
                                profile_graphics=pg).count("--profile-graphics") == 0


def t_sync_pov_transform_calls_setter_with_merged_box():
    # All three mapped slots ("pov" -> Stint/"Feed POV", "webcam" -> Program/"Solo
    # Webcam", "tyres-capture" -> Program/"Solo Tyres/Fuel Capture") must be synced
    # from one call, so capture every set_transform call.
    import tempfile
    calls = []

    def fake_set(scene, source, transform):
        calls.append({"scene": scene, "source": source, "transform": transform})
        return True, ""

    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "hud.css"), "w") as fh:
            # override only #pov's left/top; width/height fall back to the
            # hud.html base. #webcam and #tyres-capture get no override -> base
            # box only.
            fh.write("#pov { left: 1516px; top: 600px; }")
        orig = m._active_overlay_dir
        m._active_overlay_dir = lambda: d
        try:
            m._sync_pov_transform(set_transform=fake_set)
        finally:
            m._active_overlay_dir = orig

    by_source = {c["source"]: c for c in calls}
    assert set(by_source) == {"Feed POV", "Solo Webcam", "Solo Tyres/Fuel Capture"}

    pov = by_source["Feed POV"]
    assert pov["scene"] == "Stint"
    tf = pov["transform"]
    assert tf["positionX"] == 1516 and tf["positionY"] == 600   # from the override
    assert tf["boundsWidth"] == 384 and tf["boundsHeight"] == 216  # from the base
    assert tf["boundsType"] == 2 and tf["alignment"] == 5

    webcam = by_source["Solo Webcam"]
    assert webcam["scene"] == "Program"
    wtf = webcam["transform"]
    # no override for #webcam -> the hud.html base box (16:9 default).
    assert wtf["positionX"] == 14 and wtf["positionY"] == 695
    assert wtf["boundsWidth"] == 336 and wtf["boundsHeight"] == 189

    tyres = by_source["Solo Tyres/Fuel Capture"]
    assert tyres["scene"] == "Program"
    ttf = tyres["transform"]
    # no override for #tyres-capture -> the hud.html base box.
    assert ttf["positionX"] == 7 and ttf["positionY"] == 926
    assert ttf["boundsWidth"] == 245 and ttf["boundsHeight"] == 84


def t_run_module_exit_codes():
    import contextlib, io, sys as _sys, tempfile
    with tempfile.TemporaryDirectory() as td:
        script = os.path.join(td, "fake-tool.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import sys\n"
                     "def main():\n"
                     "    if sys.argv[1:] == ['--fail']:\n"
                     "        return 3\n"
                     "    if sys.argv[1:] == ['--exit-str']:\n"
                     "        sys.exit('boom guide')\n"
                     "    return None\n")
        before = list(_sys.argv)
        assert m._run_module(script, ["--fail"]) == 3
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert m._run_module(script, ["--exit-str"]) == 1
        assert "boom guide" in err.getvalue()  # sys.exit(str) must reach stderr
        assert m._run_module(script, []) == 0
        assert _sys.argv == before


def t_run_module_missing_main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        script = os.path.join(td, "no-main.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")
        assert m._run_module(script, []) == 1


def t_streams_run_feed_hidden():
    assert m.route(["streams", "run-feed", "UC1", "53001"])["verb"] == "run-feed"
    try:
        m.route(["streams", "bogus"])
    except ValueError as e:
        assert "run-feed" not in str(e)  # hidden verb is not advertised
    else:
        raise AssertionError("expected ValueError")


def t_install_tools_oneshot():
    assert m.route(["install-tools"]) == \
        {"kind": "oneshot", "command": "install-tools", "rest": []}


def t_version_route_and_dev_default():
    assert m.route(["--version"]) == {"kind": "version"}
    assert m.route(["-V"]) == {"kind": "version"}
    assert m.version() == "dev"  # repo checkout has no bundled VERSION file


def t_export_route():
    r = m.route(["export", "companion", "--out", "x"])
    assert r == {"kind": "export", "target": "companion", "rest": ["--out", "x"]}
    _raises(lambda: m.route(["export"]))
    _raises(lambda: m.route(["export", "obs"]))


def t_export_companion_writes_file():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, "buttons.companionconfig")
        m.export_companion(["--out", dst])
        assert os.path.getsize(dst) > 0


def t_export_companion_default_into_runtime():
    # No --out -> runtime/ (same home as the localized OBS collection), and the
    # dir is created on demand, NOT in the caller's cwd.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        old = m._runtime_dir
        m._runtime_dir = lambda: os.path.join(td, "runtime")
        try:
            m.export_companion([])
            out = os.path.join(td, "runtime", "racecast-buttons.companionconfig")
            assert os.path.getsize(out) > 0
        finally:
            m._runtime_dir = old


def t_install_apps_oneshot():
    assert m.route(["install-apps"]) == \
        {"kind": "oneshot", "command": "install-apps", "rest": []}


def t_update_routes_as_oneshot():
    a = m.route(["update", "--check"])
    assert a == {"kind": "oneshot", "command": "update", "rest": ["--check"]}, a


def t_update_oneshot_extra_injects_nothing():
    assert m._oneshot_extra("update", [], "/rt/demo", "/rt") == [], \
        "update needs no runtime-dir/--out injection; --current is added in oneshot()"


def t_event_routes():
    assert m.route(["event", "status"]) == \
        {"kind": "service", "command": "event", "verb": "status", "rest": []}
    assert m.route(["event", "start"])["verb"] == "start"
    assert m.route(["event", "stop"])["verb"] == "stop"
    assert m.route(["event", "status", "--no-color"])["rest"] == ["--no-color"]
    _raises(lambda: m.route(["event"]))
    _raises(lambda: m.route(["event", "restart"]))   # no restart/logs for event
    _raises(lambda: m.route(["event", "logs"]))


def t_event_dispatch_wired():
    for verb in ("status", "start", "stop"):
        assert ("event", verb) in m.DISPATCH


def t_stint_args_extraction():
    # event_start forwards only the --stint flag to the relay launch
    assert m._stint_args([]) == []
    assert m._stint_args(["--no-color"]) == []
    assert m._stint_args(["--stint", "4"]) == ["--stint", "4"]
    assert m._stint_args(["--no-color", "--stint=7"]) == ["--stint", "7"]
    assert m._stint_args(["--stint"]) == []          # missing value: let relay default


def t_stint_args_rejects_garbage():
    # fail fast BEFORE a daemon is spawned (its error would only hit the log)
    for bad in (["--stint", "abc"], ["--stint=0"], ["--stint", "-3"]):
        try:
            m._stint_args(bad)
            raise AssertionError(f"accepted {bad}")
        except SystemExit:
            pass


def t_title_args_extraction():
    # event_start forwards --title (free text) to the relay as --event-title (#207)
    assert m._title_args([]) == []
    assert m._title_args(["--qualifying"]) == []
    assert m._title_args(["--title", "Round 4"]) == ["--event-title", "Round 4"]
    assert m._title_args(["--title=Round 5 — Spa"]) == ["--event-title", "Round 5 — Spa"]
    assert m._title_args(["--stint", "3", "--title", "GTEC R4"]) == \
        ["--event-title", "GTEC R4"]
    assert m._title_args(["--title="]) == ["--event-title", ""]   # explicit clear
    assert m._title_args(["--title", "--qualifying"]) == [], \
        "bare --title followed by another flag is NOT consumed as the value"


def t_takeover_event_title_extracts():
    # A's on-air title is adopted at takeover; missing/bad -> None (leave local alone)
    assert m._takeover_event_title({"event_title": "Round 4"}) == "Round 4"
    assert m._takeover_event_title({"event_title": ""}) == ""        # clear to match A
    assert m._takeover_event_title({"live": {}}) is None             # older relay (no field)
    assert m._takeover_event_title(None) is None                     # A unreachable
    assert m._takeover_event_title({"event_title": 5}) is None       # bad type ignored


def t_relay_start_warns_when_running_and_stint_ignored():
    # already-running + --stint: must tell the operator the flag was ignored.
    # Patch every signal so relay_start_plan returns "running": pid on the port,
    # alive PID, HTTP ok, and profile match.
    import io, contextlib
    old_read = m.sv.read_pid
    old_alive = m.sv.pid_alive
    old_pids_on_port = m.pt.pids_on_port
    old_http_ok = m._relay_http_ok
    old_running = m._running_relay_profile
    old_active = m._active_profile_name
    m.sv.read_pid = lambda path: 4242
    m.sv.pid_alive = lambda pid: True
    m.pt.pids_on_port = lambda port: [4242]
    m._relay_http_ok = lambda: True
    m._running_relay_profile = lambda: "testing"
    m._active_profile_name = lambda: "testing"
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.relay_start(["--stint", "5"])
        out = buf.getvalue()
        assert "already running" in out
        assert "--stint ignored" in out and "/set/stint/5" in out
    finally:
        m.sv.read_pid = old_read
        m.sv.pid_alive = old_alive
        m.pt.pids_on_port = old_pids_on_port
        m._relay_http_ok = old_http_ok
        m._running_relay_profile = old_running
        m._active_profile_name = old_active


def _relay_spawn_stubs(http_ok_seq):
    """Patch relay_start's whole effectful surface for a plain (port-free) start so
    only the spawn/verify/retry orchestration is exercised. http_ok_seq: the booleans
    _relay_http_ok returns on successive VERIFY calls. Returns (restore_fn, calls) where
    calls['spawn'] counts start_detached invocations. wait_for is reduced to a single
    probe() so a down verify doesn't burn the real 15 s window."""
    seq = iter(http_ok_seq)
    calls = {"spawn": 0}
    saves = [
        ("pt", "pids_on_port", lambda port: []),                 # port free -> plain start
        ("sv", "read_pid", lambda path: None),
        ("sv", "pid_alive", lambda pid: False),
        ("sv", "start_detached", lambda *a, **k: (
            calls.__setitem__("spawn", calls["spawn"] + 1), 4242)[1]),
        (None, "_relay_http_ok", lambda: next(seq)),
        (None, "_running_relay_profile", lambda: ""),
        (None, "_active_profile_name", lambda: "testing"),
        (None, "_ensure_active_console_secret", lambda: None),
        (None, "_resolve_producer_name", lambda: "P"),
        (None, "_write_relay_profile_stamp", lambda: None),
        (None, "_append_tailscale_snapshot", lambda: None),
        (None, "_refresh_obs_pages", lambda *a, **k: None),
        (None, "wait_for", lambda probe, wait, **k: probe()),
    ]
    originals = []
    for obj_name, attr, val in saves:
        obj = getattr(m, obj_name) if obj_name else m
        originals.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, val)
    def restore():
        for obj, attr, old in originals:
            setattr(obj, attr, old)
    return restore, calls


def t_qualifying_mode_mismatch_note():
    # event start --qualifying but the relay came up in race mode -> warn (missing
    # Qualifying tab; the Parts control would push the wrong stream key).
    note = m.qualifying_mode_mismatch_note(True, "race")
    assert note is not None and "RACE mode" in note
    # Consistent / not requested / relay unreachable -> no warning.
    assert m.qualifying_mode_mismatch_note(True, "qualifying") is None
    assert m.qualifying_mode_mismatch_note(False, "race") is None
    assert m.qualifying_mode_mismatch_note(True, None) is None


def t_relay_start_retries_when_first_spawn_not_up():
    # The event-start race: the freshly spawned relay aborts on a still-clearing port,
    # so start_detached returns a PID for a child that died. relay_start must NOT claim
    # success on the first (failed) verify. It respawns once, and the port has
    # cleared by then.
    import io, contextlib
    restore, calls = _relay_spawn_stubs([False, True])   # down, then up on retry
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.relay_start([])
        out = buf.getvalue()
        assert calls["spawn"] == 2, f"expected one retry, got {calls['spawn']} spawns"
        assert "relay started" in out
        assert "retrying" in out
    finally:
        restore()


def t_relay_start_reports_failure_when_relay_never_comes_up():
    # If the relay never binds the control port, relay_start must report an HONEST
    # failure, never "relay started" for a dead child, which would make event-start
    # look green while the relay is down.
    import io, contextlib
    restore, calls = _relay_spawn_stubs([False, False])   # down on both attempts
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.relay_start([])
        out = buf.getvalue()
        assert calls["spawn"] == 2
        assert "ERROR" in out
        assert "relay started" not in out
    finally:
        restore()


def t_relay_stop_releases_obs_feeds_after_kill():
    # AFTER the kill: a source rebuild against a live relay would reconnect.
    # Against the dead relay it just drops the half-dead connection, freeing
    # the feed ports (otherwise FIN_WAIT_1 -> preflight "port in use").
    import io, contextlib
    calls = []
    old = (m.sv.read_pid, m.sv.pid_alive, m.sv.stop_pid, m._release_obs_feeds)
    m.sv.read_pid = lambda path: 4242
    m.sv.pid_alive = lambda pid: True
    m.sv.stop_pid = lambda pid, path, is_target=None: (
        calls.append(("stop_pid", is_target)), True)[1]
    m._release_obs_feeds = lambda: calls.append("obs")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m.relay_stop([])
        assert calls == [("stop_pid", m.sv.pid_is_relay), "obs"], \
            "the relay stop must verify process identity before killing (#102)"
    finally:
        m.sv.read_pid, m.sv.pid_alive, m.sv.stop_pid, m._release_obs_feeds = old


def t_relay_stop_skips_obs_release_when_kill_failed():
    # Relay may still be alive -> a rebuild would reconnect. Don't release.
    import io, contextlib
    calls = []
    old = (m.sv.read_pid, m.sv.pid_alive, m.sv.stop_pid, m._release_obs_feeds)
    m.sv.read_pid = lambda path: 4242
    m.sv.pid_alive = lambda pid: True
    m.sv.stop_pid = lambda pid, path, is_target=None: (calls.append("stop_pid"), False)[1]
    m._release_obs_feeds = lambda: calls.append("obs")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m.relay_stop([])
        assert calls == ["stop_pid"]
    finally:
        m.sv.read_pid, m.sv.pid_alive, m.sv.stop_pid, m._release_obs_feeds = old


def t_relay_stop_skips_obs_when_relay_not_running():
    import io, contextlib, tempfile
    calls = []
    old = (m.sv.read_pid, m.sv.pid_alive, m._release_obs_feeds, m._relay_pid_path)
    with tempfile.TemporaryDirectory() as tmp:
        m._relay_pid_path = lambda: os.path.join(tmp, "relay.pid")
        m.sv.read_pid = lambda path: None
        m.sv.pid_alive = lambda pid: False
        m._release_obs_feeds = lambda: calls.append("obs")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.relay_stop([])
            assert calls == []                  # nothing ran -> nothing to release
        finally:
            m.sv.read_pid, m.sv.pid_alive, m._release_obs_feeds, m._relay_pid_path = old


def t_streams_stop_releases_obs_feeds_when_feeds_exist():
    import io, contextlib, tempfile
    calls = []
    old = (m._release_obs_feeds, m._run_script, m._streams_static_dir)
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "feed_53001.pid"), "w") as fh:
            fh.write("1")
        m._streams_static_dir = lambda: tmp
        m._release_obs_feeds = lambda: calls.append("obs")
        m._run_script = lambda rel, args: (calls.append("run_script"), 0)[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.streams_stop([])
            assert calls == ["run_script", "obs"]   # release AFTER the feeds die
        finally:
            m._release_obs_feeds, m._run_script, m._streams_static_dir = old


def t_streams_stop_skips_obs_without_feed_pids():
    import io, contextlib, tempfile
    calls = []
    old = (m._release_obs_feeds, m._run_script, m._streams_static_dir)
    with tempfile.TemporaryDirectory() as tmp:
        m._streams_static_dir = lambda: tmp
        m._release_obs_feeds = lambda: calls.append("obs")
        m._run_script = lambda rel, args: (calls.append("run_script"), 0)[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.streams_stop([])
            assert calls == ["run_script"]
        finally:
            m._release_obs_feeds, m._run_script, m._streams_static_dir = old


def t_init_routes_with_rest():
    assert m.route(["init"]) == {"kind": "init", "rest": []}
    assert m.route(["init", "--force", "--browser", "chrome"])["rest"] == \
        ["--force", "--browser", "chrome"]


def t_env_base_per_mode():
    # mirrors _runtime_base: frozen -> next to the binary; repo -> repo root;
    # package -> the package dir itself
    assert m._env_base(True, "/opt/racecast/racecast", "/tmp/_MEIxx/src") == "/opt/racecast"
    assert m._env_base(False, "", "/repo/src") == "/repo"
    assert m._env_base(False, "", "/pkg") == "/pkg"


def t_refresh_decision():
    assert m.refresh_decision(None, None) == "skip-no-pages"
    assert m.refresh_decision(None, "abc") == "skip-no-pages"
    assert m.refresh_decision("abc", "abc") == "skip-unchanged"
    assert m.refresh_decision("abc", "old") == "refresh"
    assert m.refresh_decision("abc", None) == "refresh"          # first run
    assert m.refresh_decision("abc", "abc", force=True) == "refresh"


def t_served_pages_hash_concatenates_in_order():
    import hashlib
    pages = {p: p.encode() for p in m.OBS_PAGE_PATHS}
    expected = hashlib.sha256(b"".join(pages[p] for p in m.OBS_PAGE_PATHS)).hexdigest()
    assert m.served_pages_hash(fetch=lambda p: pages[p]) == expected


def t_served_pages_hash_none_when_any_fetch_fails():
    def fetch(path):
        if path == "/hud":
            raise OSError("connection refused")
        return b"HUD"
    assert m.served_pages_hash(fetch=fetch) is None


def t_served_pages_hash_none_when_override_css_fetch_fails():
    def fetch(path):
        if path == "/hud/override.css":
            raise OSError("connection refused")
        return b"x"
    assert m.served_pages_hash(fetch=fetch) is None


def t_served_pages_hash_none_when_first_fetch_fails():
    def fetch(path):
        raise OSError("connection refused")
    assert m.served_pages_hash(fetch=fetch) is None


def t_pages_hash_roundtrip_and_missing():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state", "obs-pages.hash")
        assert m.read_pages_hash(path) is None                   # missing file
        m.write_pages_hash(path, "abc123")                       # creates the dir
        assert m.read_pages_hash(path) == "abc123"


def t_wait_for_polls_until_deadline():
    ticks = iter([0, 1, 2, 3, 4, 5])
    slept = []
    ok = m.wait_for(lambda: False, 2, clock=lambda: next(ticks),
                    sleep=slept.append)
    assert ok is False
    assert slept                                                 # polled, not busy-spun
    assert m.wait_for(lambda: True, 0, clock=lambda: 0,
                      sleep=lambda s: None) is True               # checks at least once


def t_route_ui():
    assert m.route(["ui"]) == {"kind": "ui", "rest": []}
    assert m.route(["ui", "--no-browser"]) == {"kind": "ui", "rest": ["--no-browser"]}


def t_profile_routing():
    assert m.route(["profile", "list"]) == {"kind": "profile", "rest": ["list"]}
    assert m.route(["profile", "new", "erf", "--from", "example"]) == {
        "kind": "profile", "rest": ["new", "erf", "--from", "example"]}
    # an unknown profile verb is NOT validated at the route seam (parse_profile_args
    # does that); route just hands the rest through:
    assert m.route(["profile", "bogus"]) == {"kind": "profile", "rest": ["bogus"]}


def t_oneshot_extra_paths():
    rd = os.path.join("R", "demo")   # profile runtime
    base = "R"                       # base runtime (cookies/preflight)
    assert m._oneshot_extra("graphics", [], rd, base) == [
        "--out", os.path.join(rd, "graphics")]
    assert m._oneshot_extra("media", [], rd, base) == [
        "--out", os.path.join(rd, "media")]
    assert m._oneshot_extra("setup", [], rd, base) == [
        "--out", os.path.join(rd, "GT_Racing_Endurance.import.json"),
        "--media", os.path.join(rd, "media"),
        "--graphics", os.path.join(rd, "graphics")]
    assert m._oneshot_extra("cookies", [], rd, base) == ["--runtime-dir", base]
    assert m._oneshot_extra("preflight", [], rd, base) == ["--runtime-dir", base]
    assert m._oneshot_extra("graphics", ["--out", "X"], rd, base) == []  # user override respected


def t_oneshot_extra_media_injects_cookies_when_present():
    # `racecast media` must hand get-media the REAL top-level cookie jar so the
    # frozen binary stops 403-ing Intro/Outro (get-media's here-relative fallback
    # resolves into the PyInstaller bundle). Injected only when the jar exists and
    # the user didn't pass their own --cookies.
    import tempfile
    with tempfile.TemporaryDirectory() as base:
        rd = os.path.join(base, "demo")
        open(os.path.join(base, "yt-cookies.txt"), "w").close()
        assert m._oneshot_extra("media", [], rd, base) == [
            "--out", os.path.join(rd, "media"),
            "--cookies", os.path.join(base, "yt-cookies.txt")]
        # legacy jar name is honored too
    with tempfile.TemporaryDirectory() as base:
        rd = os.path.join(base, "demo")
        open(os.path.join(base, "cookies.txt"), "w").close()
        assert m._oneshot_extra("media", [], rd, base) == [
            "--out", os.path.join(rd, "media"),
            "--cookies", os.path.join(base, "cookies.txt")]


def t_oneshot_extra_media_no_cookies_when_absent_or_user_supplied():
    # No jar on disk -> no injection (existing behavior preserved).
    assert m._oneshot_extra("media", [], os.path.join("/rt", "demo"), "/rt") == [
        "--out", os.path.join("/rt", "demo", "media")]
    # A user-supplied --cookies wins: nothing auto-injected.
    import tempfile
    with tempfile.TemporaryDirectory() as base:
        rd = os.path.join(base, "demo")
        open(os.path.join(base, "yt-cookies.txt"), "w").close()
        assert m._oneshot_extra("media", ["--cookies", "/my/jar"], rd, base) == [
            "--out", os.path.join(rd, "media")]
def t_oneshot_extra_setup_out_is_kind_aware():
    # The `setup` default --out filename depends on the active profile kind. (#304)
    # endurance (explicit or the default when unset) keeps the byte-identical name;
    # solo gets its own template basename. An explicit --out always wins regardless.
    rd = os.path.join("R", "demo")
    base = "R"
    assert m._oneshot_extra("setup", [], rd, base, kind="endurance") == [
        "--out", os.path.join(rd, "GT_Racing_Endurance.import.json"),
        "--media", os.path.join(rd, "media"),
        "--graphics", os.path.join(rd, "graphics")]
    assert m._oneshot_extra("setup", [], rd, base, kind="solo") == [
        "--out", os.path.join(rd, "GT_Racing_Solo.import.json"),
        "--media", os.path.join(rd, "media"),
        "--graphics", os.path.join(rd, "graphics")]
    # no kind passed -> falls back to RACECAST_KIND from the environment (endurance
    # when unset/blank/unknown, matching setup-assets' own default).
    saved = os.environ.pop("RACECAST_KIND", None)
    try:
        assert m._oneshot_extra("setup", [], rd, base) == [
            "--out", os.path.join(rd, "GT_Racing_Endurance.import.json"),
            "--media", os.path.join(rd, "media"),
            "--graphics", os.path.join(rd, "graphics")]
        os.environ["RACECAST_KIND"] = "solo"
        assert m._oneshot_extra("setup", [], rd, base) == [
            "--out", os.path.join(rd, "GT_Racing_Solo.import.json"),
            "--media", os.path.join(rd, "media"),
            "--graphics", os.path.join(rd, "graphics")]
    finally:
        if saved is None:
            os.environ.pop("RACECAST_KIND", None)
        else:
            os.environ["RACECAST_KIND"] = saved
    # explicit --out still wins over the kind-aware default.
    assert m._oneshot_extra("setup", ["--out", "z", "--media", "m", "--graphics", "g"],
                            rd, base, kind="solo") == []


def t_setup_import_name_by_kind():
    # The kind-aware setup import filename is shared logic: both _oneshot_extra and
    # the init wizard's "is setup already done?" probe (_init_import_json) must
    # resolve to the SAME basename for a given kind. (#304)
    assert m._setup_import_name("solo") == "GT_Racing_Solo.import.json"
    assert m._setup_import_name("endurance") == "GT_Racing_Endurance.import.json"
    assert m._setup_import_name("") == "GT_Racing_Endurance.import.json"


def t_init_import_json_is_kind_aware():
    # _init_import_json must follow RACECAST_KIND the same way _oneshot_extra's
    # setup default does, or a solo profile (whose setup writes
    # GT_Racing_Solo.import.json) never reads as done and the wizard re-runs it.
    # (#304)
    saved = os.environ.pop("RACECAST_KIND", None)
    try:
        os.environ["RACECAST_KIND"] = "solo"
        assert m._init_import_json().endswith("GT_Racing_Solo.import.json")
        os.environ["RACECAST_KIND"] = "endurance"
        assert m._init_import_json().endswith("GT_Racing_Endurance.import.json")
        os.environ.pop("RACECAST_KIND", None)
        assert m._init_import_json().endswith("GT_Racing_Endurance.import.json")
    finally:
        if saved is None:
            os.environ.pop("RACECAST_KIND", None)
        else:
            os.environ["RACECAST_KIND"] = saved


def t_brands_oneshot_mapping_and_out():
    assert m.ONESHOT_MAP["brands"] == "relay/get-brands.py"
    assert "brands" in m.ONESHOTS
    extra = m._oneshot_extra("brands", [], os.path.join("/rt", "demo"), "/rt")
    assert extra == ["--out", os.path.join("/rt", "demo", "brands")], extra
    assert m._oneshot_extra("brands", ["--out", "/x"], os.path.join("/rt", "demo"), "/rt") == [], \
        "an explicit --out from the user wins (no injected default)"


def t_profile_env_vars_filters_empty():
    rc = m.pcfg.ResolvedConfig(
        profile="demo", name="Demo", sheet_id="abc",
        sheet_push_url="", intro_url="https://i", outro_url="")
    assert m._profile_env_vars(rc) == {
        "RACECAST_SHEET_ID": "abc", "RACECAST_INTRO_URL": "https://i",
        "RACECAST_PROFILE_NAME": "Demo", "RACECAST_KIND": "endurance"}


def t_profile_env_vars_includes_obs_collection():
    rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo League", sheet_id="abc",
                               obs_collection="Demo Broadcast")
    out = m._profile_env_vars(rc)
    assert out["RACECAST_OBS_COLLECTION"] == "Demo Broadcast"


def t_profile_env_vars_includes_event_title():
    rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc",
                               event_title="GTEC - Round 4")
    assert m._profile_env_vars(rc)["RACECAST_EVENT_TITLE"] == "GTEC - Round 4"
    # empty -> filtered out (relay falls back to event.json / no title)
    rc2 = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc")
    assert "RACECAST_EVENT_TITLE" not in m._profile_env_vars(rc2)


def t_profile_env_vars_includes_trailer_url():
    rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc",
                               trailer_url="https://youtu.be/TTT")
    assert m._profile_env_vars(rc)["RACECAST_TRAILER_URL"] == "https://youtu.be/TTT"
    # empty -> filtered out
    rc2 = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc")
    assert "RACECAST_TRAILER_URL" not in m._profile_env_vars(rc2)


def t_profile_env_vars_includes_discord_webhook():
    rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc",
                               discord_webhook_url="https://discord.com/api/webhooks/1/x")
    assert m._profile_env_vars(rc)["RACECAST_DISCORD_WEBHOOK_URL"] == \
        "https://discord.com/api/webhooks/1/x"
    # empty -> filtered out (push disabled)
    rc2 = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc")
    assert "RACECAST_DISCORD_WEBHOOK_URL" not in m._profile_env_vars(rc2)


def t_active_obs_collection_falls_back_to_constant_without_profile():
    import tempfile
    import obs_ws
    saved = dict(os.environ)
    try:
        os.environ.pop("RACECAST_PROFILE", None)
        with tempfile.TemporaryDirectory() as td:
            orig = m._env_base
            m._env_base = lambda *a, **k: td      # empty root -> no profile resolves
            try:
                assert m._active_obs_collection() == obs_ws.EXPECTED_SCENE_COLLECTION
            finally:
                m._env_base = orig
    finally:
        os.environ.clear(); os.environ.update(saved)


def t_profile_runtime_scoping():
    assert m._profile_runtime("/r", "demo") == os.path.join("/r", "demo")
    assert m._profile_runtime("/r", "erf") == os.path.join("/r", "erf")
    assert m._profile_runtime("/r", None) == "/r"


def t_profiles_data_reports_active_logo_flag():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles")
        os.makedirs(os.path.join(prof, "demo"))
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(prof, "demo", "profile.env"), "w") as fh:
            fh.write("NAME=Demo League\nLOGO=logo.png\n")
        with open(os.path.join(prof, "demo", "logo.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nFAKE")              # any bytes; isfile is what matters
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            with_logo = m.profiles_data()
            os.remove(os.path.join(prof, "demo", "logo.png"))   # file gone -> flag false
            without_logo = m.profiles_data()
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert with_logo["ok"] is True and with_logo["logo"] is True
        assert without_logo["logo"] is False


def t_profiles_data_lists_active_and_available():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles")
        os.makedirs(os.path.join(prof, "demo"))
        os.makedirs(os.path.join(prof, "erf"))
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(prof, "demo", "profile.env"), "w") as fh:
            fh.write("NAME=Demo League\nSHEET_ID=abc\n")
        with open(os.path.join(prof, "erf", "profile.env"), "w") as fh:
            fh.write("NAME=ERF\n")
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profiles_data()
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d["ok"] is True
        assert d["active"] == "demo"
        names = {p["name"]: p for p in d["profiles"]}
        assert names["demo"]["display"] == "Demo League"
        assert names["demo"]["sheet_set"] is True
        assert names["erf"]["sheet_set"] is False


def t_profiles_data_reports_kind():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles")
        os.makedirs(os.path.join(prof, "demo"))
        os.makedirs(os.path.join(prof, "solo1"))
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(prof, "demo", "profile.env"), "w") as fh:
            fh.write("NAME=Demo League\nSHEET_ID=abc\n")
        with open(os.path.join(prof, "solo1", "profile.env"), "w") as fh:
            fh.write("NAME=Solo One\nKIND=solo\nTEMPLATE=commentary\n")
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profiles_data()
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        by = {p["name"]: p for p in d["profiles"]}
        assert by["demo"]["kind"] == "endurance", by["demo"]
        assert by["solo1"]["kind"] == "solo", by["solo1"]


def t_profile_use_data_switches_pointer():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles", "demo")
        os.makedirs(prof)
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(prof, "profile.env"), "w") as fh:
            fh.write("SHEET_ID=abc\n")
        os.makedirs(os.path.join(td, "runtime"))
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profile_use_data("demo")
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d == {"ok": True, "active": "demo"}
        with open(os.path.join(td, "runtime", "active-profile")) as fh:
            assert fh.read().strip() == "demo"


def t_profile_use_data_unknown_is_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, ".env.example"), "w").close()
        os.makedirs(os.path.join(td, "runtime"))
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profile_use_data("nope")
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d["ok"] is False and d["error"]


def t_profile_new_data_creates_from_example():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ex = os.path.join(td, "profiles", "example")
        os.makedirs(ex)
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(ex, "profile.env"), "w") as fh:
            fh.write("NAME=Example\nSHEET_ID=\n")
        orig_b = m._env_base
        m._env_base = lambda *a, **k: td
        try:
            d = m.profile_new_data("gt3", "example")
        finally:
            m._env_base = orig_b
        assert d["ok"] is True and d["name"] == "gt3"
        assert os.path.isfile(os.path.join(td, "profiles", "gt3", "profile.env"))


def t_profile_new_data_spaced_name_returns_slug():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ex = os.path.join(td, "profiles", "example")
        os.makedirs(ex)
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(ex, "profile.env"), "w") as fh:
            fh.write("NAME=Example\nSHEET_ID=\n")
        orig_b = m._env_base
        m._env_base = lambda *a, **k: td
        try:
            d = m.profile_new_data("Demo League", "example")
        finally:
            m._env_base = orig_b
        assert d["ok"] is True and d["name"] == "demo-league"   # slug, switchable via `use`
        assert os.path.isfile(os.path.join(td, "profiles", "demo-league", "profile.env"))


def t_profile_new_data_bad_name_is_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "profiles", "example"))
        open(os.path.join(td, ".env.example"), "w").close()
        open(os.path.join(td, "profiles", "example", "profile.env"), "w").close()
        orig_b = m._env_base
        m._env_base = lambda *a, **k: td
        try:
            d = m.profile_new_data("!!!", "example")   # slugifies to empty -> rejected
        finally:
            m._env_base = orig_b
        assert d["ok"] is False and d["error"]


def t_profile_new_data_forwards_kind_template():
    seen = {}
    def fake_create(root, name, source, kind=None, template=None):
        seen.update(root=root, name=name, source=source, kind=kind,
                    template=template)
        return os.path.join(root, "profiles", "solo1")
    orig = m._env_base
    m._env_base = lambda *a, **k: "/tmp/x"
    try:
        r = m.profile_new_data("Solo One", None, create=fake_create,
                               kind="solo", template="pov")
    finally:
        m._env_base = orig
    assert r["ok"] is True, r
    assert seen["kind"] == "solo" and seen["template"] == "pov", seen


def t_profile_new_data_defaults_endurance():
    seen = {}
    def fake_create(root, name, source, kind=None, template=None):
        seen.update(kind=kind, template=template)
        return os.path.join(root, "profiles", "gt3")
    orig = m._env_base
    m._env_base = lambda *a, **k: "/tmp/x"
    try:
        m.profile_new_data("GT3", "demo", create=fake_create)
    finally:
        m._env_base = orig
    assert seen["kind"] == m.pcfg.DEFAULT_KIND and seen["template"] is None, seen


def t_profile_env_entries_data_reads_active():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles", "demo")
        os.makedirs(prof)
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(prof, "profile.env"), "w") as fh:
            fh.write("# comment\nSHEET_ID=abc\nNAME=Demo\n")
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profile_env_entries_data()
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d["ok"] is True and d["active"] == "demo"
        keys = [e["key"] for e in d["entries"]]
        assert "SHEET_ID" in keys and "NAME" in keys


def t_profile_env_entries_data_no_profile_is_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, ".env.example"), "w").close()
        os.makedirs(os.path.join(td, "runtime"))
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profile_env_entries_data()
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d["ok"] is False and d["error"]


def t_profile_env_write_data_persists_to_active():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles", "demo")
        os.makedirs(prof)
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(prof, "profile.env"), "w") as fh:
            fh.write("# keep me\nSHEET_ID=old\n")
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profile_env_write_data([{"key": "SHEET_ID", "value": "new"},
                                          {"key": "OBS_COLLECTION", "value": "GT3"}])
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d["ok"] is True
        with open(os.path.join(prof, "profile.env")) as fh:
            text = fh.read()
        assert "SHEET_ID=new" in text and "OBS_COLLECTION=GT3" in text
        assert "# keep me" in text


def t_profile_env_write_data_no_profile_is_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, ".env.example"), "w").close()
        os.makedirs(os.path.join(td, "runtime"))
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.profile_env_write_data([{"key": "SHEET_ID", "value": "x"}])
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d["ok"] is False and d["error"]
        assert not os.path.exists(os.path.join(td, ".env")), \
            "the machine .env must never be touched when no profile is active"


def t_overlay_read_absent_ok_empty():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles", "demo")
        os.makedirs(prof)
        open(os.path.join(prof, "profile.env"), "w").close()
        open(os.path.join(td, ".env.example"), "w").close()
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            d = m.overlay_read_data("hud")
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert d["ok"] is True and d["css"] == "" and d["page"] == "hud"


def t_overlay_write_then_read_roundtrip():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles", "demo")
        os.makedirs(prof)
        open(os.path.join(prof, "profile.env"), "w").close()
        open(os.path.join(td, ".env.example"), "w").close()
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            w = m.overlay_write_data("hud", "#stint{left:5px}")
            d = m.overlay_read_data("hud")
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert w["ok"] is True
        assert d["ok"] is True and d["css"] == "#stint{left:5px}"
        on_disk = os.path.join(td, "profiles", "demo", "overlay", "hud.css")
        assert os.path.exists(on_disk)


def t_overlay_rejects_unknown_page():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles", "demo")
        os.makedirs(prof)
        open(os.path.join(prof, "profile.env"), "w").close()
        open(os.path.join(td, ".env.example"), "w").close()
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            assert m.overlay_write_data("timer", "x")["ok"] is False
            assert m.overlay_write_data("panel", "x")["ok"] is False
            assert m.overlay_read_data("../etc")["ok"] is False
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r


def t_overlay_no_profile_is_error():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, ".env.example"), "w").close()
        os.makedirs(os.path.join(td, "runtime"))
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            assert m.overlay_read_data("hud")["ok"] is False
            assert m.overlay_write_data("hud", "x")["ok"] is False
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r


def _mk_active_profile(td):
    """Temp profile 'demo' + active pointer; returns the patched (env_base,
    runtime_base) restorers' originals via a tuple. Mirrors the overlay tests."""
    prof = os.path.join(td, "profiles", "demo")
    os.makedirs(prof)
    open(os.path.join(prof, "profile.env"), "w").close()
    open(os.path.join(td, ".env.example"), "w").close()
    os.makedirs(os.path.join(td, "runtime"))
    with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
        fh.write("demo\n")
    orig = (m._env_base, m._runtime_base_dir)
    m._env_base = lambda *a, **k: td
    m._runtime_base_dir = lambda: os.path.join(td, "runtime")
    return orig


def t_overlay_layout_write_compiles_and_persists():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            w = m.overlay_layout_write_data(
                "hud", {"page": "hud",
                        "slots": {"stint": {"left": 800, "top": 30, "fontSize": 44}}})
            r = m.overlay_layout_read_data("hud")
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert w["ok"] is True and "left: 800px" in w["css"] and "font-size: 44px" in w["css"]
        od = os.path.join(td, "profiles", "demo", "overlay")
        assert os.path.exists(os.path.join(od, "hud.css"))
        assert os.path.exists(os.path.join(od, "layout-hud.json"))
        # relay serves the generated hud.css verbatim -> the compiled CSS is on disk
        with open(os.path.join(od, "hud.css"), encoding="utf-8") as fh:
            assert "left: 800px" in fh.read()
        assert r["ok"] and r["migrated"] is False
        assert r["layout"]["slots"]["stint"]["left"] == 800


def t_overlay_layout_read_migrates_handwritten_css():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        od = os.path.join(td, "profiles", "demo", "overlay")
        os.makedirs(od)
        with open(os.path.join(od, "hud.css"), "w", encoding="utf-8") as fh:
            fh.write("#stint{left:999px}/* mine */")
        try:
            r = m.overlay_layout_read_data("hud")    # no layout-hud.json yet
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert r["ok"] and r["migrated"] is True
        assert r["layout"]["customCss"] == "#stint{left:999px}/* mine */"
        assert r["layout"]["slots"] == {}


def t_overlay_layout_folds_legacy_timer_css():
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        od = os.path.join(td, "profiles", "demo", "overlay")
        os.makedirs(od)
        # Write a layout-hud.json so migration path is skipped (we test the fold path)
        with open(os.path.join(od, "layout-hud.json"), "w", encoding="utf-8") as fh:
            _json.dump({"page": "hud", "slots": {}, "fonts": [], "customCss": ""}, fh)
        # Write a real timer.css with actual CSS rules
        with open(os.path.join(od, "timer.css"), "w", encoding="utf-8") as fh:
            fh.write("#clock { color: #f4f4f4; }")
        try:
            d = m.overlay_layout_read_data("hud")
            # Idempotency: re-reading must not accumulate another copy of the timer CSS.
            d2 = m.overlay_layout_read_data("hud")
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert d["ok"] is True
        assert "#clock { color: #f4f4f4; }" in (d["layout"].get("customCss") or "")
        assert (d2["layout"].get("customCss") or "").count("#clock") == 1


def t_overlay_layout_ignores_comment_only_timer_css():
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        od = os.path.join(td, "profiles", "demo", "overlay")
        os.makedirs(od)
        # Write a layout-hud.json
        with open(os.path.join(od, "layout-hud.json"), "w", encoding="utf-8") as fh:
            _json.dump({"page": "hud", "slots": {}, "fonts": [], "customCss": ""}, fh)
        # Write a comment-only timer.css (the default scaffold, no real rules)
        with open(os.path.join(od, "timer.css"), "w", encoding="utf-8") as fh:
            fh.write("/* just the template, no rules */\n")
        try:
            d = m.overlay_layout_read_data("hud")
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert d["ok"] is True
        assert "template" not in (d["layout"].get("customCss") or "")


def t_overlay_layout_write_rejects_unknown_page():
    assert m.overlay_layout_write_data("panel", {"page": "panel"})["ok"] is False


def t_overlay_slots_data_from_real_hud():
    r = m.overlay_slots_data("hud")
    assert r["ok"] is True
    assert any(s["id"] == "stint" for s in r["slots"])
    assert "#stint" in r["css"] and r["sample"]["stint"]


def t_overlay_slots_data_includes_flag_presets():
    r = m.overlay_slots_data("hud")
    assert r["ok"], r
    fp = r["flagPresets"]
    assert isinstance(fp, list) and fp, "flagPresets must be a non-empty list"
    assert all(isinstance(p, dict) and "state" in p and "label" in p for p in fp)
    assert "safety-car" in [p["state"] for p in fp]


def t_overlay_fonts_upload_then_list_and_serve():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            up = m.overlay_font_upload_data("League.woff2", b"OTTOfontbytes")
            bad = m.overlay_font_upload_data("../evil.woff2", b"x")
            badext = m.overlay_font_upload_data("League.exe", b"x")
            listing = m.overlay_fonts_list_data()
            served = m.overlay_font_serve("League.woff2")
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert up["ok"] is True and up["name"] == "League.woff2"
        assert bad["ok"] is False and badext["ok"] is False
        assert listing["ok"] and listing["fonts"] == ["League.woff2"]
        assert served and served[1] == "font/woff2"
        assert os.path.basename(served[0]) == "League.woff2"


def t_machine_font_download_into_library():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        css = ('@font-face{font-family:Oswald;'
               'src:url(https://fonts.gstatic.com/s/oswald/v1/x.woff2) format("woff2")}')
        try:
            d = m.machine_font_download_data(
                "Oswald", css_fetch=lambda u: css, bin_fetch=lambda u: b"WOFF2DATA")
            lib = m.machine_fonts_list_data()
            ov = m.overlay_fonts_list_data()
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert d["ok"] is True and d["name"] == "Oswald.woff2"
        # lands in the machine-wide library, NOT the profile, until used
        assert os.path.exists(os.path.join(td, "runtime", "fonts", "Oswald.woff2"))
        assert "Oswald.woff2" in lib["fonts"] and "catalog" not in lib
        assert "Oswald.woff2" in ov["library"] and "Oswald.woff2" not in ov["fonts"]


def _latin_cuts_css():
    """A css2 response with the four latin cuts (regular/bold/italic/bold-italic)
    plus a cyrillic block that must be dropped (no U+0000-00FF)."""
    def block(style, weight, fn, latin=True):
        ur = "U+0000-00FF, U+0131" if latin else "U+0400-045F"
        return ("@font-face { font-family:'Nunito Sans'; font-style:%s; font-weight:%s;"
                " src: url(https://fonts.gstatic.com/s/ns/%s) format('woff2');"
                " unicode-range: %s; }" % (style, weight, fn, ur))
    return "\n".join([
        block("normal", "400", "cyr.woff2", latin=False),
        block("normal", "400", "reg.woff2"),
        block("normal", "700", "bold.woff2"),
        block("italic", "400", "ital.woff2"),
        block("italic", "700", "bolditalic.woff2"),
    ])


def t_machine_font_download_saves_all_cuts():
    # The cuts path self-hosts regular + bold + italic + bold-italic so a slot can
    # render TRUE bold/italic; the base name is returned for the library entry.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            d = m.machine_font_download_data(
                "Nunito Sans",
                css_fetch=lambda u: _latin_cuts_css(),
                bin_fetch=lambda u: b"WOFF2")
            lib = m.machine_fonts_list_data()["fonts"]
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert d["ok"] is True and d["name"] == "NunitoSans.woff2"
        fdir = os.path.join(td, "runtime", "fonts")
        for fn in ("NunitoSans.woff2", "NunitoSans-Bold.woff2",
                   "NunitoSans-Italic.woff2", "NunitoSans-BoldItalic.woff2"):
            assert os.path.exists(os.path.join(fdir, fn)), fn
            assert fn in lib
        assert "NunitoSanscyr.woff2" not in lib              # cyrillic block dropped


def t_machine_font_download_single_cut_fallback():
    # A response without latin subset blocks (the legacy/stub shape) still self-hosts
    # one file, back-compat for families the cuts request cannot satisfy.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            d = m.machine_font_download_data(
                "Oswald", css_fetch=lambda u: "url(https://fonts.gstatic.com/x.woff2)",
                bin_fetch=lambda u: b"DATA")
            lib = m.machine_fonts_list_data()["fonts"]
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert d["ok"] is True and d["name"] == "Oswald.woff2"
        assert lib == ["Oswald.woff2"]


def t_machine_font_delete_removes_whole_family():
    # Deleting a base family also removes its cut siblings (else a half-deleted family
    # would break: a slot referencing it loses its bold/italic faces).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            m.machine_font_download_data(
                "Nunito Sans", css_fetch=lambda u: _latin_cuts_css(),
                bin_fetch=lambda u: b"WOFF2")
            d = m.machine_font_delete_data("NunitoSans.woff2")
            after = m.machine_fonts_list_data()["fonts"]
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert d["ok"] is True
        assert after == []                                   # base + all siblings gone


def t_overlay_save_copies_font_cut_siblings_into_profile():
    # Copy-on-save pulls the WHOLE family (base + bold + italic) into the profile so
    # `profile export` carries true bold/italic offline, and the CSS groups them.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            m.machine_font_download_data(
                "Nunito Sans", css_fetch=lambda u: _latin_cuts_css(),
                bin_fetch=lambda u: b"WOFF2")
            w = m.overlay_layout_write_data(
                "hud", {"page": "hud", "slots": {"stint": {"fontFamily": "NunitoSans"}}})
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert w["ok"] is True
        pdir = os.path.join(td, "profiles", "demo", "overlay", "fonts")
        for fn in ("NunitoSans.woff2", "NunitoSans-Bold.woff2",
                   "NunitoSans-Italic.woff2", "NunitoSans-BoldItalic.woff2"):
            assert os.path.exists(os.path.join(pdir, fn)), fn
        assert 'font-family: "NunitoSans"' in w["css"] and "font-style: italic" in w["css"]


def t_machine_font_download_rejects_unsafe_name():
    # SSRF gate: a name with path/host tricks is rejected before any fetch.
    hit = {"n": 0}
    d = m.machine_font_download_data(
        "../etc/passwd", css_fetch=lambda u: hit.__setitem__("n", 1) or "x",
        bin_fetch=lambda u: b"x")
    assert d["ok"] is False and "invalid" in d["error"] and hit["n"] == 0


def t_machine_font_download_allows_uncurated_name():
    # Any syntactically valid family (not just the catalog) is fetchable.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            d = m.machine_font_download_data(
                "Big Shoulders Display",
                css_fetch=lambda u: "url(https://fonts.gstatic.com/x.woff2)",
                bin_fetch=lambda u: b"DATA")
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert d["ok"] is True and d["name"] == "BigShouldersDisplay.woff2"


def t_machine_font_download_no_woff2_is_error():
    d = m.machine_font_download_data(
        "Oswald", css_fetch=lambda u: "/* nothing */", bin_fetch=lambda u: b"")
    assert d["ok"] is False


def t_machine_font_delete_removes_from_library():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            m.machine_font_download_data(
                "Oswald", css_fetch=lambda u: "url(https://fonts.gstatic.com/x.woff2)",
                bin_fetch=lambda u: b"DATA")
            before = m.machine_fonts_list_data()["fonts"]
            d = m.machine_font_delete_data("Oswald.woff2")
            after = m.machine_fonts_list_data()["fonts"]
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert "Oswald.woff2" in before and d["ok"] is True
        assert "Oswald.woff2" not in after


def t_google_font_catalog_parses_metadata():
    meta = ('{"familyMetadataList":[{"family":"Oswald"},{"family":"Big Shoulders Display"},'
            '{"family":"Bad/Name"},{"family":"Roboto"}]}')
    d = m.google_font_catalog_data(fetch=lambda: meta)
    assert d["ok"] is True and d["source"] == "google"
    assert "Oswald" in d["families"] and "Big Shoulders Display" in d["families"]
    assert "Bad/Name" not in d["families"]                 # unfetchable names filtered out
    assert d["families"] == sorted(d["families"])          # sorted for the typeahead


def t_google_font_catalog_falls_back_to_curated():
    def boom():
        raise OSError("offline")
    d = m.google_font_catalog_data(fetch=boom)
    assert d["ok"] is True and d["source"] == "curated"
    assert d["families"] == list(m.ob.GOOGLE_FONTS)


def t_overlay_save_copies_referenced_library_font_into_profile():
    # Copy-on-save: a design that references a library font copies it into the
    # profile (portable export) and emits its @font-face in the generated CSS.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            m.machine_font_download_data(
                "Oswald", css_fetch=lambda u: "url(https://fonts.gstatic.com/x.woff2)",
                bin_fetch=lambda u: b"DATA")
            w = m.overlay_layout_write_data(
                "hud", {"page": "hud",
                        "slots": {"stint": {"fontFamily": "Oswald"}}})
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert w["ok"] is True
        copied = os.path.join(td, "profiles", "demo", "overlay", "fonts", "Oswald.woff2")
        assert os.path.exists(copied)                 # copied into the profile
        assert "@font-face" in w["css"] and 'font-family: "Oswald"' in w["css"]
        assert 'url(/overlay/fonts/Oswald.woff2)' in w["css"]


def t_overlay_bg_path_present_and_absent():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        orig = _mk_active_profile(td)
        try:
            assert m.overlay_bg_path() is None         # no Overlay.png yet
            g = os.path.join(td, "runtime", "demo", "graphics")
            os.makedirs(g)
            with open(os.path.join(g, "Overlay.png"), "wb") as fh:
                fh.write(b"\x89PNG")
            hit = m.overlay_bg_path()
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert hit and hit.endswith("Overlay.png")


def t_obs_page_paths_include_overrides():
    assert m.OBS_PAGE_PATHS == ("/hud", "/hud/override.css",
                                "/splitscreen", "/splitscreen/override.css",
                                "/intermission", "/intermission/override.css")


def t_obs_page_paths_relay_mirror_in_sync():
    # OBS_PAGE_PATHS lives in racecast.py (the obs-refresh hash gate reads it); the
    # relay keeps a mirror constant only for test discoverability. Pin the two equal
    # so a future overlay page added to one can never silently drift from the other.
    spec2 = importlib.util.spec_from_file_location(
        "feeds_opp", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    feeds = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(feeds)
    assert tuple(feeds.OBS_PAGE_PATHS) == tuple(m.OBS_PAGE_PATHS)


def t_obs_ws_persist_default_on():
    # Persistent obs-websocket connections default ON; a falsey
    # RACECAST_OBS_WS_PERSIST restores connect-per-call. (#537)
    spec2 = importlib.util.spec_from_file_location(
        "feeds_persist", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    feeds = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(feeds)
    assert feeds.obs_ws_persist_enabled({}) is True
    assert feeds.obs_ws_persist_enabled({"RACECAST_OBS_WS_PERSIST": "0"}) is False


def t_relay_runtime_args_adds_overlay_when_dir_exists():
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        od = os.path.join(tmp, "overlay"); os.makedirs(od)
        assert m._overlay_relay_args(od) == ["--overlay-dir", od]


def t_relay_runtime_args_omits_overlay_when_absent():
    assert m._overlay_relay_args(None) == []
    assert m._overlay_relay_args("/no/such/overlay/dir") == []


def t_servable_logo_path_allows_web_images_only():
    for p in ("a/logo.png", "a/logo.JPG", "x.jpeg", "x.webp", "x.gif", "brand.svg"):
        assert m.servable_logo_path(p) == p          # web image -> passed through
    for p in ("", "profile.env", "notes.txt", "clip.mp4", "a/logo", "x.PNG.bak"):
        assert m.servable_logo_path(p) == ""          # not a web image -> blanked


def t_profile_logo_returns_active_servable_path():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        prof = os.path.join(td, "profiles")
        os.makedirs(os.path.join(prof, "demo"))
        open(os.path.join(td, ".env.example"), "w").close()
        with open(os.path.join(prof, "demo", "profile.env"), "w") as fh:
            fh.write("NAME=Demo\nLOGO=logo.svg\n")
        logo = os.path.join(prof, "demo", "logo.svg")
        with open(logo, "wb") as fh:
            fh.write(b"<svg/>")
        os.makedirs(os.path.join(td, "runtime"))
        with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
            fh.write("demo\n")
        orig_b, orig_r = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            got = m.profile_logo()
            os.remove(logo)                # file gone -> None
            gone = m.profile_logo()
        finally:
            m._env_base, m._runtime_base_dir = orig_b, orig_r
        assert got == logo
        assert gone is None


def t_route_backup():
    assert m.route(["backup", "list"]) == {"kind": "backup", "rest": ["list"]}
    assert m.route(["backup", "create", "Winter"]) == {"kind": "backup", "rest": ["create", "Winter"]}


def _raises(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def t_event_start_gate_blocks_without_force():
    # A static-precondition FAIL aborts bring-up (exit 1) BEFORE any service is
    # launched, when --force is absent.
    ev = m._event_modules()[0]
    orig = m._event_gate_results
    m._event_gate_results = lambda e, p: [ev.Result(ev.FAIL, ".env", "missing RACECAST_SHEET_ID")]
    try:
        try:
            m.event_start([])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert e.code == 1
    finally:
        m._event_gate_results = orig


def t_event_start_force_skips_gate():
    # --force bypasses the gate even with a FAIL present: event_start proceeds to
    # step 1 (_tailscale_connect), which we stub to prove the gate was skipped.
    ev = m._event_modules()[0]
    orig_gate, orig_ts = m._event_gate_results, m._tailscale_connect

    class _Reached(Exception):
        pass

    m._event_gate_results = lambda e, p: [ev.Result(ev.FAIL, ".env", "x")]
    def _boom(_ev):
        raise _Reached()
    m._tailscale_connect = _boom
    try:
        try:
            m.event_start(["--force"])
            raise AssertionError("expected to reach _tailscale_connect")
        except _Reached:
            pass                                # got past the gate
        except SystemExit:
            raise AssertionError("--force should have skipped the gate") from None
    finally:
        m._event_gate_results, m._tailscale_connect = orig_gate, orig_ts


def t_takeover_plan_relay_reachable_race():
    st = {"live": {"feed": "A", "stint": 5, "mode": "race"}}
    p = m.takeover_plan(st)
    assert p == {"stint": 5, "qualifying": False, "source": "relay"}


def t_takeover_plan_relay_reachable_qualifying():
    st = {"live": {"feed": "A", "stint": 1, "mode": "qualifying"}}
    assert m.takeover_plan(st)["qualifying"] is True


def t_takeover_plan_override_beats_relay():
    st = {"live": {"feed": "A", "stint": 5, "mode": "race"}}
    p = m.takeover_plan(st, stint_override=8)
    assert p == {"stint": 8, "qualifying": False, "source": "override"}


def t_takeover_plan_qualifying_flag_forces_mode():
    st = {"live": {"feed": "A", "stint": 5, "mode": "race"}}
    assert m.takeover_plan(st, qualifying_flag=True)["qualifying"] is True
    # override + flag
    assert m.takeover_plan(None, stint_override=3, qualifying_flag=True) == {
        "stint": 3, "qualifying": True, "source": "override"}


def t_takeover_plan_unreachable_no_override_is_sheet_none():
    assert m.takeover_plan(None) == {"stint": None, "qualifying": False, "source": "sheet"}
    # a status dict from an older relay without a live block also yields None
    assert m.takeover_plan({"feeds": {}})["stint"] is None


def t_league_guard():
    # same league -> allowed
    assert m.league_guard("SHEET1", "SHEET1", force=False) is None
    # mismatch -> blocked with a message naming both
    msg = m.league_guard("SHEETA", "SHEETB", force=False)
    assert msg and "SHEETA" in msg and "SHEETB" in msg
    # --force overrides
    assert m.league_guard("SHEETA", "SHEETB", force=True) is None
    # unknown id (older relay / unset) -> cannot verify, allow
    assert m.league_guard(None, "SHEETB", force=False) is None
    assert m.league_guard("SHEETA", "", force=False) is None


def t_event_takeover_routing():
    r = m.route(["event", "takeover", "100.64.1.2", "--stint", "5"])
    assert r["verb"] == "takeover" and r["rest"] == ["100.64.1.2", "--stint", "5"]


def _with_env(**kv):
    """Set env keys (None deletes), return a restore callable."""
    saved = dict(os.environ)
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return lambda: (os.environ.clear(), os.environ.update(saved))


def t_event_takeover_blocks_league_mismatch():
    orig = m._relay_fetch_json
    restore = _with_env(RACECAST_SHEET_ID="SHEET_B")
    m._relay_fetch_json = lambda url, timeout=3: {
        "league": {"sheet_id": "SHEET_A"}, "live": {"feed": "A", "stint": 3, "mode": "race"}}
    try:
        try:
            m.event_takeover(["100.64.1.2"])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "league mismatch" in str(e.code)
    finally:
        m._relay_fetch_json = orig
        restore()


def t_event_takeover_unreachable_without_stint_errors():
    orig = m._relay_fetch_json
    restore = _with_env(RACECAST_SHEET_ID=None, RACECAST_SHEET_PUSH_URL="x")
    def _boom(url, timeout=3):
        raise OSError("refused")
    m._relay_fetch_json = _boom
    try:
        try:
            m.event_takeover(["100.64.1.2"])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "--stint" in str(e.code)
    finally:
        m._relay_fetch_json = orig
        restore()


def t_event_takeover_success_calls_event_start():
    orig_fetch, orig_es, orig_chat = m._relay_fetch_json, m.event_start, m.chat_cmd
    restore = _with_env(RACECAST_SHEET_ID="S", RACECAST_SHEET_PUSH_URL="https://push")
    m._relay_fetch_json = lambda url, timeout=3: {
        "league": {"sheet_id": "S"}, "live": {"feed": "B", "stint": 7, "mode": "race"}}
    m.chat_cmd = lambda rest: None
    captured = {}
    m.event_start = lambda a, **kw: captured.update(args=a, kw=kw)
    try:
        m.event_takeover(["100.64.1.2"])
        assert captured["args"] == ["--stint", "7"]
        # a takeover must NOT reset the report session window (continuity on B)
        assert captured["kw"].get("_new_session") is False, captured["kw"]
    finally:
        m._relay_fetch_json, m.event_start, m.chat_cmd = orig_fetch, orig_es, orig_chat
        restore()


def t_is_continuation_start():
    # a fresh event start (no flags) begins a new report session
    assert m._is_continuation_start([]) is False
    assert m._is_continuation_start(["--force", "--title", "GP"]) is False
    # a mid-event bring-up (--stint / --part, both spellings) continues it
    assert m._is_continuation_start(["--stint", "7"]) is True
    assert m._is_continuation_start(["--stint=7"]) is True
    assert m._is_continuation_start(["--part", "2"]) is True
    assert m._is_continuation_start(["--part=2"]) is True


def t_event_takeover_qualifying_and_override_forwarded():
    orig_fetch, orig_es, orig_chat = m._relay_fetch_json, m.event_start, m.chat_cmd
    restore = _with_env(RACECAST_SHEET_ID="S", RACECAST_SHEET_PUSH_URL="https://push")
    m._relay_fetch_json = lambda url, timeout=3: {
        "league": {"sheet_id": "S"}, "live": {"feed": "A", "stint": 2, "mode": "race"}}
    m.chat_cmd = lambda rest: None
    captured = {}
    m.event_start = lambda a, **kw: captured.__setitem__("args", a)
    try:
        m.event_takeover(["100.64.1.2", "--stint", "9", "--qualifying"])
        assert captured["args"] == ["--stint", "9", "--qualifying"]   # override wins, mode forced
    finally:
        m._relay_fetch_json, m.event_start, m.chat_cmd = orig_fetch, orig_es, orig_chat
        restore()


def t_event_takeover_pulls_event_title():
    # Takeover adopts producer A's on-air event title (#207), persisting it to
    # event.json BEFORE bring-up so the new relay loads it (mirrors the chat pull).
    import json as _json, tempfile
    orig_fetch, orig_es, orig_chat = m._relay_fetch_json, m.event_start, m.chat_cmd
    orig_path = m._event_title_path
    restore = _with_env(RACECAST_SHEET_ID="S", RACECAST_SHEET_PUSH_URL="https://push")
    m._relay_fetch_json = lambda url, timeout=3: {
        "league": {"sheet_id": "S"}, "live": {"feed": "A", "stint": 3, "mode": "race"},
        "event_title": "GTEC - Round 4 - Nürburgring"}
    m.chat_cmd = lambda rest: None
    m.event_start = lambda a, **kw: None
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "event.json")
        m._event_title_path = lambda: path
        try:
            m.event_takeover(["100.64.1.2"])
            with open(path, encoding="utf-8") as fh:
                assert _json.load(fh) == {"title": "GTEC - Round 4 - Nürburgring"}
        finally:
            (m._relay_fetch_json, m.event_start, m.chat_cmd,
             m._event_title_path) = orig_fetch, orig_es, orig_chat, orig_path
            restore()


def t_funnel_takeover_base_builds_console_url():
    assert m._funnel_takeover_base("producer-a.example.ts.net") == \
        "https://producer-a.example.ts.net/console/takeover"
    assert m._funnel_takeover_base("https://producer-a.example.ts.net/console") == \
        "https://producer-a.example.ts.net/console/takeover"
    assert m._funnel_takeover_base("http://producer-a.example.ts.net/") == \
        "https://producer-a.example.ts.net/console/takeover"


def t_event_takeover_funnel_requires_secret():
    # --funnel with no CONSOLE_SECRET in the active profile -> clear abort.
    restore = _with_env(RACECAST_CONSOLE_SECRET="", RACECAST_SHEET_ID="L1")
    try:
        try:
            m.event_takeover(["producer-a.example.ts.net", "--funnel", "--stint", "3"])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            msg = str(e.code).lower() if e.code else ""
            assert "secret" in msg or "console_secret" in msg
    finally:
        restore()


def t_event_takeover_funnel_auth_rejected_aborts():
    # A 403/401 from the funnel endpoint means a bad or missing secret, so abort
    # rather than silently falling back the way a network failure does.
    import urllib.error
    def fake_get(url, secret=None, timeout=5):
        raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)
    restore = _with_env(RACECAST_CONSOLE_SECRET="S", RACECAST_SHEET_ID="L1")
    orig_get, m._takeover_get = m._takeover_get, fake_get
    orig_es, m.event_start = m.event_start, lambda a, **kw: (_ for _ in ()).throw(
        AssertionError("event_start must not run on auth-reject"))
    try:
        try:
            m.event_takeover(["producer-a.example.ts.net", "--funnel", "--stint", "3"])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            msg = str(e.code).lower() if e.code else ""
            assert "secret" in msg or "rejected" in msg
    finally:
        m._takeover_get, m.event_start = orig_get, orig_es
        restore()


def t_event_takeover_funnel_401_blames_old_relay_not_secret():
    # A 401 (not 403) means A is an OLDER relay that still requires a console
    # token, so the abort message must NOT blame the secret.
    import urllib.error
    def fake_get(url, secret=None, timeout=5):
        raise urllib.error.HTTPError(url, 401, "unauthorized", {}, None)
    restore = _with_env(RACECAST_CONSOLE_SECRET="S", RACECAST_SHEET_ID="L1")
    orig_get, m._takeover_get = m._takeover_get, fake_get
    orig_es, m.event_start = m.event_start, lambda a, **kw: (_ for _ in ()).throw(
        AssertionError("event_start must not run on auth-reject"))
    try:
        try:
            m.event_takeover(["producer-a.example.ts.net", "--funnel", "--stint", "3"])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            msg = str(e.code).lower() if e.code else ""
            assert "401" in msg, msg
            assert "older" in msg or "update" in msg, msg
            assert "console_secret" not in msg, msg   # must NOT misattribute to the secret
    finally:
        m._takeover_get, m.event_start = orig_get, orig_es
        restore()


def t_event_takeover_funnel_success_calls_event_start():
    seen = {}
    def fake_get(url, secret=None, timeout=5):
        seen.setdefault("urls", []).append(url)
        seen["secret"] = secret
        if url.endswith("/status"):
            return {"live": {"feed": "A", "stint": 3, "mode": "race"},
                    "league": {"sheet_id": "L1"}, "event_title": "", "timer": None}
        if url.endswith("/chat"):
            return {"messages": []}
        if url.endswith("/versions"):
            return {"versions": {}}
        return {}
    restore = _with_env(RACECAST_CONSOLE_SECRET="S", RACECAST_SHEET_ID="L1",
                        RACECAST_SHEET_PUSH_URL="https://push")
    orig_get, m._takeover_get = m._takeover_get, fake_get
    # The funnel path resolves the active profile (for the CONSOLE_SECRET), which
    # would otherwise inject the shipped `demo` league's SHEET_ID over our env and
    # trip the league guard. Neutralise that injection so we test the takeover path,
    # not profile resolution (the real _active_console_secret is covered elsewhere).
    orig_apply, m._apply_active_profile_env = m._apply_active_profile_env, lambda: None
    es = {}
    orig_es, m.event_start = m.event_start, lambda a, **kw: es.update(args=a)
    try:
        m.event_takeover(["producer-a.example.ts.net", "--funnel"])
    finally:
        m._takeover_get, m.event_start = orig_get, orig_es
        m._apply_active_profile_env = orig_apply
        restore()
    assert es.get("args") == ["--stint", "3"], es           # derived from A's live.stint
    assert all(u.startswith("https://producer-a.example.ts.net/console/takeover")
               for u in seen["urls"])
    assert seen["secret"] == "S"                            # step-up header sent


def t_chat_routing():
    # verb is passed through in rest; route() does not validate it (chat_cmd does)
    assert m.route(["chat", "clear"]) == {"kind": "chat", "rest": ["clear"]}
    assert m.route(["chat", "export", "--out", "/tmp/x.json"]) == {
        "kind": "chat", "rest": ["export", "--out", "/tmp/x.json"]}
    assert m.route(["chat", "pull", "100.64.1.2"]) == {
        "kind": "chat", "rest": ["pull", "100.64.1.2"]}
    # bare "chat" (no verb) routes correctly; chat_cmd handles the usage error
    assert m.route(["chat"]) == {"kind": "chat", "rest": []}


def t_discord_routing():
    # verb is passed through in rest; route() does not validate it (discord_cmd does)
    assert m.route(["discord", "join"]) == {"kind": "discord", "rest": ["join"]}
    assert m.route(["discord", "leave"]) == {"kind": "discord", "rest": ["leave"]}
    assert m.route(["discord", "status"]) == {"kind": "discord", "rest": ["status"]}
    # bare "discord" (no verb) routes correctly; discord_cmd defaults to "status"
    assert m.route(["discord"]) == {"kind": "discord", "rest": []}


def t_discord_voice_target_sheet_failure_falls_back_to_env():
    # Any Sheet-fetch failure (network, 4xx, bad CSV) must be swallowed and never
    # propagate; the env RACECAST_DISCORD_VOICE_URL is the fallback target.
    import http_util as _hu
    saved = dict(os.environ)
    orig_get_bytes = _hu.get_bytes

    def boom(*a, **k):
        raise OSError("no net")

    try:
        os.environ["RACECAST_SHEET_ID"] = "SOME_SHEET_ID"
        os.environ["RACECAST_DISCORD_VOICE_URL"] = "https://discord.com/channels/9/8"
        _hu.get_bytes = boom
        assert m._discord_voice_target() == ("9", "8")
    finally:
        _hu.get_bytes = orig_get_bytes
        os.environ.clear(); os.environ.update(saved)


def t_discord_cmd_join_uses_resolved_target():
    import io, contextlib
    orig_client, orig_target = m._discord_voice_client, m._discord_voice_target
    calls = {}

    class _FakeClient:
        def join(self, guild, channel):
            calls["join"] = (guild, channel)
            return True, "joined #general"

    m._discord_voice_client = _FakeClient
    m._discord_voice_target = lambda: ("111", "222")
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.discord_cmd(["join"])
        assert calls["join"] == ("111", "222")
        assert rc == 0
        assert "joined #general" in buf.getvalue()
    finally:
        m._discord_voice_client, m._discord_voice_target = orig_client, orig_target


def t_discord_cmd_join_without_target_exits():
    orig_client, orig_target = m._discord_voice_client, m._discord_voice_target
    m._discord_voice_client = object   # never reaches .join()
    m._discord_voice_target = lambda: None
    try:
        try:
            m.discord_cmd(["join"])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert "no voice channel configured" in str(e.code)
    finally:
        m._discord_voice_client, m._discord_voice_target = orig_client, orig_target


def t_discord_cmd_leave_does_not_resolve_target():
    import io, contextlib
    orig_client, orig_target = m._discord_voice_client, m._discord_voice_target
    calls = {}

    class _FakeClient:
        def leave(self):
            calls["left"] = True
            return True, "left the voice channel"

    m._discord_voice_client = _FakeClient
    def _boom():
        raise AssertionError("leave must not resolve a voice target")
    m._discord_voice_target = _boom
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.discord_cmd(["leave"])
        assert calls.get("left") is True
        assert rc == 0
        assert "left the voice channel" in buf.getvalue()
    finally:
        m._discord_voice_client, m._discord_voice_target = orig_client, orig_target


def t_discord_cmd_leave_failure_returns_nonzero():
    orig_client = m._discord_voice_client

    class _FakeClient:
        def leave(self):
            return False, "Discord not running"

    m._discord_voice_client = _FakeClient
    try:
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            rc = m.discord_cmd(["leave"])
        assert rc == 1
    finally:
        m._discord_voice_client = orig_client


def t_discord_cmd_status_reports_configured_target():
    import io, contextlib
    orig_client, orig_target = m._discord_voice_client, m._discord_voice_target
    m._discord_voice_client = object
    m._discord_voice_target = lambda: ("111", "222")
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.discord_cmd(["status"])
        assert rc == 0
        assert "111#222" in buf.getvalue()
    finally:
        m._discord_voice_client, m._discord_voice_target = orig_client, orig_target


def t_discord_cmd_status_reports_none_configured():
    import io, contextlib
    orig_client, orig_target = m._discord_voice_client, m._discord_voice_target
    m._discord_voice_client = object
    m._discord_voice_target = lambda: None
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.discord_cmd(["status"])
        assert rc == 0
        assert "none configured" in buf.getvalue()
    finally:
        m._discord_voice_client, m._discord_voice_target = orig_client, orig_target


def t_discord_cmd_unknown_verb_prints_usage():
    try:
        m.discord_cmd(["bogus"])
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert str(e.code) == "usage: racecast discord {join|leave|status}"


def t_discord_cmd_requires_client_credentials():
    # join/leave build the client and require creds; status does NOT (it is read-only).
    restore = _with_env(RACECAST_DISCORD_CLIENT_ID=None, RACECAST_DISCORD_CLIENT_SECRET=None)
    try:
        for verb in ("join", "leave"):
            try:
                m.discord_cmd([verb])
                raise AssertionError("expected SystemExit for " + verb)
            except SystemExit as e:
                assert "DISCORD_CLIENT_ID/SECRET" in str(e.code)
    finally:
        restore()


def t_discord_cmd_status_without_credentials_is_readonly():
    # status must never require creds or sys.exit; it reports the target and a
    # note that the Discord app is not configured.
    import io, contextlib
    orig_target = m._discord_voice_target
    m._discord_voice_target = lambda: None
    restore = _with_env(RACECAST_DISCORD_CLIENT_ID=None, RACECAST_DISCORD_CLIENT_SECRET=None)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.discord_cmd(["status"])
        assert rc == 0
        assert "no DISCORD_CLIENT_ID/SECRET" in buf.getvalue()
    finally:
        m._discord_voice_target = orig_target
        restore()


def t_main_dispatches_discord_join_leave_status():
    orig = m.discord_cmd
    captured = {}
    m.discord_cmd = lambda rest: captured.setdefault("rest", rest)
    try:
        for verb in ("join", "leave", "status"):
            captured.clear()
            m.main(["discord", verb])
            assert captured["rest"] == [verb]
    finally:
        m.discord_cmd = orig


def t_set_env_key_preserves_other_keys():
    """_set_env_key must NOT drop other keys or comments: passing a single pair to
    the full-set _write_env_file wipes everything else."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "profile.env")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# header comment\nNAME=Demo\nSHEET_ID=abc123\n"
                     "SHEET_PUSH_URL=https://x/exec\n")
        res = m._set_env_key(p, "CONSOLE_SECRET", "deadbeef")
        assert res.get("ok"), res
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        got = m.parse_env_text(text)
        assert got["NAME"] == "Demo"
        assert got["SHEET_ID"] == "abc123"            # NOT wiped
        assert got["SHEET_PUSH_URL"] == "https://x/exec"
        assert got["CONSOLE_SECRET"] == "deadbeef"    # added
        assert "# header comment" in text             # comments preserved
        # updating an existing key keeps the others too
        m._set_env_key(p, "SHEET_ID", "newid")
        with open(p, encoding="utf-8") as fh:
            got = m.parse_env_text(fh.read())
        assert got["SHEET_ID"] == "newid" and got["NAME"] == "Demo"
        assert got["CONSOLE_SECRET"] == "deadbeef"


def t_route_cockpit():
    assert m.route(["console", "token", "revoke", "Alpha"]) == {
        "kind": "console", "rest": ["token", "revoke", "Alpha"]}
    # `links` is a top-level command and `funnel` no longer exists, so like the
    # removed enable/disable verbs both are rejected at route() time. (#216)
    for bad in (["console"], ["console", "bogus"], ["console", "enable"],
                ["console", "disable"], ["console", "funnel", "on"],
                ["console", "links"]):
        try:
            m.route(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def t_route_links():
    assert m.route(["links"]) == {"kind": "links", "rest": []}
    assert m.route(["links", "--post"]) == {"kind": "links", "rest": ["--post"]}


def t_links_roster_union():
    # People = Schedule ∪ Crew, deduped by streamer_key (== asset_key), schedule
    # first. A crew-only director joins the list; a person in both appears once.
    orig_sched = m._console_roster
    orig_crew = m._crew_roster
    try:
        m._console_roster = lambda: ["Alice", "Bob"]          # schedule (streamers)
        m._crew_roster = lambda: ["Bob", "Dana the Director"]  # crew tab
        assert m._links_roster() == ["Alice", "Bob", "Dana the Director"]
    finally:
        m._console_roster = orig_sched
        m._crew_roster = orig_crew


def t_links_cmd_prints_share_url_and_redirect_uri():
    # links_cmd must print a bare share URL and OAuth redirect URI after the
    # per-person link list.
    import io, sys as _sys
    orig_roster = m._links_roster
    orig_magic = m._tailscale_magicdns
    orig_ip = m._tailscale_ip
    orig_secret = m._ensure_active_console_secret
    orig_versions = m.cpadm.load_versions
    orig_apply = m._apply_active_profile_env
    orig_versions_path = m._console_versions_path
    try:
        m._links_roster = lambda: ["Alice"]
        m._tailscale_magicdns = lambda: "my-host.ts.net"
        m._tailscale_ip = lambda: "100.64.0.1"
        m._ensure_active_console_secret = lambda: "testsecret"
        m.cpadm.load_versions = lambda p: {}
        m._apply_active_profile_env = lambda: None
        m._console_versions_path = lambda: "/tmp/versions.json"
        buf = io.StringIO()
        old_stdout = _sys.stdout
        _sys.stdout = buf
        try:
            m.links_cmd([])
        finally:
            _sys.stdout = old_stdout
        out = buf.getvalue()
        assert "/console/oauth/callback" in out, out
        assert "/console" in out, out
    finally:
        m._links_roster = orig_roster
        m._tailscale_magicdns = orig_magic
        m._tailscale_ip = orig_ip
        m._ensure_active_console_secret = orig_secret
        m.cpadm.load_versions = orig_versions
        m._apply_active_profile_env = orig_apply
        m._console_versions_path = orig_versions_path


def t_route_funnel():
    assert m.route(["funnel", "on"]) == {"kind": "funnel", "rest": ["on"]}
    assert m.route(["funnel", "off"]) == {"kind": "funnel", "rest": ["off"]}
    # Validation of on|off happens in funnel_cmd, not route(): route stays a
    # pure pass-through for the funnel command (like chat/profile).
    assert m.route(["funnel"]) == {"kind": "funnel", "rest": []}


def t_funnel_auto_enabled_gate():
    # _funnel_auto_enabled gates `event start` auto-bringup. The Funnel is now
    # OPT-OUT: it auto-enables by default and only stays down when the machine
    # flag RACECAST_FUNNEL (legacy RACECAST_COCKPIT_FUNNEL) is explicitly falsey
    # (false/0/no/off). It still requires a usable cockpit. The gate reads
    # console_status_data(), whose real shape is {ok, has_secret, ...} with NO
    # "enabled" key; a gate on st["enabled"] makes the whole auto-enable path dead.
    # (#216)
    import tempfile
    orig_env_file = m._env_file
    orig_status = m.console_status_data
    try:
        with tempfile.TemporaryDirectory() as d:
            epath = os.path.join(d, ".env")
            m._env_file = lambda: epath
            usable = {"ok": True, "has_secret": True}
            m.console_status_data = lambda: usable

            # Flag absent entirely (empty .env) + usable console -> True
            # (opt-out default).
            with open(epath, "w", encoding="utf-8") as fh:
                fh.write("")
            assert m._funnel_auto_enabled() is True

            # No .env file at all + usable console -> True (same opt-out default).
            os.remove(epath)
            assert m._funnel_auto_enabled() is True

            # Empty value -> True (treated as absent, not a falsey override).
            with open(epath, "w", encoding="utf-8") as fh:
                fh.write("RACECAST_FUNNEL=\n")
            assert m._funnel_auto_enabled() is True

            # Explicit truthy flag -> True.
            with open(epath, "w", encoding="utf-8") as fh:
                fh.write("RACECAST_FUNNEL=true\n")
            assert m._funnel_auto_enabled() is True

            # Every falsey form explicitly opts OUT -> False.
            for falsey in ("false", "0", "no", "off", "FALSE", "Off"):
                with open(epath, "w", encoding="utf-8") as fh:
                    fh.write("RACECAST_FUNNEL=%s\n" % falsey)
                assert m._funnel_auto_enabled() is False, falsey

            # Legacy env name still honored as the opt-out lever.
            with open(epath, "w", encoding="utf-8") as fh:
                fh.write("RACECAST_COCKPIT_FUNNEL=off\n")
            assert m._funnel_auto_enabled() is False

            # Default-on but console not usable (no secret) -> False.
            with open(epath, "w", encoding="utf-8") as fh:
                fh.write("")
            m.console_status_data = lambda: {"ok": True, "has_secret": False}
            assert m._funnel_auto_enabled() is False
    finally:
        m._env_file = orig_env_file
        m.console_status_data = orig_status


def t_cookies_twitch_routing():
    # "twitch" as the first token selects the Twitch export
    args = m._cookies_oneshot_args(["twitch", "firefox"])
    assert "--platform" in args and args[args.index("--platform") + 1] == "twitch"
    assert "firefox" in args
    # no leading "twitch" -> YouTube flow unchanged (no --platform injected)
    args2 = m._cookies_oneshot_args(["firefox"])
    assert "--platform" not in args2 and "firefox" in args2
    assert m._cookies_oneshot_args([]) == [], "empty list is unchanged"


def t_freeport_route():
    assert m.route(["freeport"]) == {"kind": "freeport", "rest": []}
    assert m.route(["freeport", "53001", "--force"]) == \
        {"kind": "freeport", "rest": ["53001", "--force"]}


def t_freeport_parse_args_defaults_and_flags():
    assert m.parse_freeport_args([]) == ([53001, 53002, 53003], False)
    assert m.parse_freeport_args(["53001"]) == ([53001], False)
    assert m.parse_freeport_args(["53002", "--force"]) == ([53002], True)
    assert m.parse_freeport_args(["-f", "53003"]) == ([53003], True)


def t_freeport_parse_args_rejects_bad_tokens():
    _raises(lambda: m.parse_freeport_args(["nope"]))
    _raises(lambda: m.parse_freeport_args(["70000"]))      # out of range
    _raises(lambda: m.parse_freeport_args(["--what"]))


def t_freeport_owner_running_relay_owns_feed_and_control_ports():
    assert m.freeport_owner(53001, relay_alive=True, static_alive_ports=set()) == "relay"
    assert m.freeport_owner(m.RELAY_PORT, relay_alive=True, static_alive_ports=set()) == "relay"
    # relay down -> not owned by the relay
    assert m.freeport_owner(53001, relay_alive=False, static_alive_ports=set()) is None


def t_freeport_owner_static_feed():
    assert m.freeport_owner(53002, relay_alive=False, static_alive_ports={53002}) == "streams"
    assert m.freeport_owner(53003, relay_alive=False, static_alive_ports={53002}) is None


def t_overlay_asset_serve_resolves_and_guards():
    # The builder canvas previews bundled HUD flags/brands offline (no relay).
    flag = m.overlay_asset_serve("flags", "belgium")
    assert flag and os.path.exists(flag[0]) and flag[1] == "image/svg+xml"
    brand = m.overlay_asset_serve("brands", "bmw")
    assert brand and os.path.exists(brand[0]) and brand[1] == "image/png"
    # strict key + subdir whitelist -> traversal / unknown sub / missing -> None
    assert m.overlay_asset_serve("flags", "../config") is None
    assert m.overlay_asset_serve("evil", "bmw") is None
    assert m.overlay_asset_serve("brands", "definitely-not-a-brand") is None
    assert m.overlay_asset_serve("flags", "Bel/gium") is None


def t_route_speedtest_is_oneshot():
    assert m.route(["speedtest"]) == {"kind": "oneshot", "command": "speedtest", "rest": []}
    assert m.route(["speedtest", "--json"]) == \
        {"kind": "oneshot", "command": "speedtest", "rest": ["--json"]}


def t_speedtest_is_a_runtime_dir_oneshot():
    # It must receive --runtime-dir <base> so history lands at the machine-level runtime.
    assert "speedtest" in m.RUNTIME_DIR_ONESHOTS
    assert m.ONESHOT_MAP["speedtest"] == "scripts/speedtest.py"


def t_speedtest_op_registered():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "ui"))
    import ui_ops
    assert ui_ops.OPS["speedtest"] == ["speedtest"]


def t_speedtest_data_shape():
    import tempfile
    d = tempfile.mkdtemp()
    # seed one record through the speedtest module the provider reads
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "scripts"))
    import speedtest as st
    st.append_record({"ts": 1, "download_mbps": 50.0, "upload_mbps": 20.0,
                      "ping_ms": 9.0, "jitter_ms": 1.0, "packet_loss": 0.0,
                      "server": "S", "isp": "I", "result_url": None}, d)
    out = m.speedtest_data(base_dir=d)
    assert out["ok"] is True
    assert out["latest"]["download_mbps"] == 50.0
    assert len(out["history"]) == 1
    # thresholds travel with the response so the UI badge can't drift from them
    assert out["thresholds"] == {"min_down": 25.0, "min_up": 10.0,
                                 "rec_down": 50.0, "rec_up": 20.0}


def t_companion_cmds_linux_uses_systemd_when_unit_present():
    import companion_linux as cl
    orig_platform = m.sys.platform
    orig_detect = cl.detect_unit
    try:
        m.sys.platform = "linux"
        cl.detect_unit = lambda *a, **k: "companion"
        cmds = m._companion_cmds(m._companion())
        assert cmds["running"] == ["systemctl", "is-active", "companion"]
    finally:
        m.sys.platform = orig_platform
        cl.detect_unit = orig_detect


def t_companion_cmds_linux_none_without_unit():
    import companion_linux as cl
    orig_platform = m.sys.platform
    orig_detect = cl.detect_unit
    try:
        m.sys.platform = "linux"
        cl.detect_unit = lambda *a, **k: None
        assert m._companion_cmds(m._companion()) is None
    finally:
        m.sys.platform = orig_platform
        cl.detect_unit = orig_detect


def t_unsupported_msg_linux_is_no_service_wording():
    orig = m.sys.platform
    try:
        m.sys.platform = "linux"
        msg = m._companion_unsupported_msg()
        assert "no companion.service" in msg.lower()
    finally:
        m.sys.platform = orig


def _linux_companion_env(detect="companion", ts="100.64.1.2", run_calls=None):
    """Monkeypatch racecast for the Linux companion_start/stop branch. Returns
    (teardown, cl). `run_calls` collects argv passed to subprocess.run."""
    import companion_linux as cl
    saved = {
        "platform": m.sys.platform, "detect": cl.detect_unit,
        "ts": m._tailscale_ip, "run": m.subprocess.run,
        "running": m._companion_running,
    }
    m.sys.platform = "linux"
    cl.detect_unit = lambda *a, **k: detect
    m._tailscale_ip = lambda: ts
    m._companion_running = lambda cc: False

    class P:
        returncode = 0
    def fake_run(argv, **kw):
        if run_calls is not None:
            run_calls.append(argv)
        return P()
    m.subprocess.run = fake_run

    def teardown():
        m.sys.platform = saved["platform"]
        cl.detect_unit = saved["detect"]
        m._tailscale_ip = saved["ts"]
        m.subprocess.run = saved["run"]
        m._companion_running = saved["running"]
    return teardown, cl


def t_companion_start_linux_calls_helper_with_tailscale_ip():
    calls = []
    teardown, cl = _linux_companion_env(ts="100.64.1.2", run_calls=calls)
    orig_exists = m.os.path.exists
    try:
        m.os.path.exists = lambda p: True   # helper present (enable-control done)
        m.companion_start(["auto"])
    finally:
        m.os.path.exists = orig_exists
        teardown()
    assert ["sudo", "-n", cl.HELPER_PATH, "100.64.1.2"] in calls


def t_companion_start_linux_falls_back_to_localhost_without_tailscale():
    calls = []
    teardown, cl = _linux_companion_env(ts=None, run_calls=calls)
    orig_exists = m.os.path.exists
    try:
        m.os.path.exists = lambda p: True
        m.companion_start(["auto"])
    finally:
        m.os.path.exists = orig_exists
        teardown()
    assert ["sudo", "-n", cl.HELPER_PATH, "127.0.0.1"] in calls


def t_companion_start_linux_guides_when_not_enabled():
    calls = []
    teardown, cl = _linux_companion_env(run_calls=calls)
    orig_exists = m.os.path.exists
    raised = False
    try:
        m.os.path.exists = lambda p: False  # helper absent -> enable-control needed
        try:
            m.companion_start(["auto"])
        except SystemExit:
            raised = True
    finally:
        m.os.path.exists = orig_exists
        teardown()
    assert not any(cl.HELPER_PATH in " ".join(c) for c in calls)
    assert raised


def t_companion_stop_linux_runs_systemctl_stop():
    calls = []
    teardown, cl = _linux_companion_env(run_calls=calls)
    try:
        m._companion_running = lambda cc: True
        m.companion_stop([])
    finally:
        teardown()
    assert ["sudo", "-n", "systemctl", "stop", "companion"] in calls


def t_companion_enable_control_routes():
    assert m.route(["companion", "enable-control"])["verb"] == "enable-control"


def t_companion_enable_control_is_dispatchable():
    assert ("companion", "enable-control") in m.DISPATCH


def t_function_local_peer_imports_are_frozen():
    """Every peer module racecast.py imports INSIDE a function (lazy import) must be
    declared as a PyInstaller --hidden-import in tools/build-binary.py. PyInstaller's
    static scan misses function-local imports, so a missing one makes the frozen
    binary raise ModuleNotFoundError at runtime, and binary-smoke does not catch a
    lazily-imported, error-swallowed path."""
    import ast
    src_dir = os.path.join(ROOT, "src")
    scripts_dir = os.path.join(src_dir, "scripts")

    def is_peer(name):
        return (os.path.exists(os.path.join(scripts_dir, name + ".py"))
                or os.path.exists(os.path.join(src_dir, name + ".py")))

    with open(os.path.join(src_dir, "racecast.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    local_imports = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Import):
                    local_imports |= {a.name for a in node.names if is_peer(a.name)}

    with open(os.path.join(ROOT, "tools", "build-binary.py"), encoding="utf-8") as fh:
        build_src = fh.read()
    missing = sorted(m for m in local_imports if f'"{m}"' not in build_src)
    assert not missing, ("peer modules imported function-locally in racecast.py but "
                         f"not --hidden-import in tools/build-binary.py: {missing}")


def t_path_loaded_module_imports_are_frozen():
    """src/ui/*.py is loaded by PATH, not imported as a package, so PyInstaller's
    scan never walks it, so a peer module imported there is invisible to the build
    even at module level. It must be a --hidden-import unless racecast.py already
    imports it at module level, where the scan picks it up via that route."""
    import ast
    src_dir = os.path.join(ROOT, "src")
    scripts_dir = os.path.join(src_dir, "scripts")

    def is_peer(name):
        return os.path.exists(os.path.join(scripts_dir, name + ".py"))

    def imported_names(path, top_level_only=False):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        nodes = tree.body if top_level_only else ast.walk(tree)
        found = set()
        for node in nodes:
            if isinstance(node, ast.Import):
                found |= {a.name for a in node.names}
        return found

    seen_by_scan = imported_names(os.path.join(src_dir, "racecast.py"),
                                  top_level_only=True)
    with open(os.path.join(ROOT, "tools", "build-binary.py"), encoding="utf-8") as fh:
        build_src = fh.read()

    missing = set()
    ui_dir = os.path.join(src_dir, "ui")
    for name in sorted(os.listdir(ui_dir)):
        if not name.endswith(".py"):
            continue
        for mod in imported_names(os.path.join(ui_dir, name)):
            if is_peer(mod) and mod not in seen_by_scan and f'"{mod}"' not in build_src:
                missing.add(mod)
    assert not missing, ("peer modules imported by path-loaded src/ui modules but "
                         f"not --hidden-import in tools/build-binary.py: {sorted(missing)}")


# Event-title providers for the Control Center Home. (#207)

def t_event_title_read_from_relay_when_alive():
    d = m.event_title_read_data(alive=lambda: True,
                                fetch=lambda u: {"event_title": "Round 4"})
    assert d == {"ok": True, "title": "Round 4",
                 "source": "relay", "relay_alive": True}, d


def t_event_title_read_relay_unreachable_falls_back_to_file():
    # alive() True but the GET blows up -> read the persisted file instead.
    import json, tempfile
    with tempfile.TemporaryDirectory() as dd:
        p = os.path.join(dd, "event.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"title": "From File"}, fh)
        def boom(_u):
            raise OSError("relay starting up")
        d = m.event_title_read_data(alive=lambda: True, fetch=boom,
                                    path=p, default="Default Cup")
        assert d["title"] == "From File" and d["source"] == "file", d


def t_event_title_read_file_then_default_when_relay_down():
    import json, tempfile
    with tempfile.TemporaryDirectory() as dd:
        p = os.path.join(dd, "event.json")
        # no file -> profile default
        d = m.event_title_read_data(alive=lambda: False, path=p, default="Default Cup")
        assert d == {"ok": True, "title": "Default Cup",
                     "source": "default", "relay_alive": False}, d
        # file present -> file wins
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"title": "From File"}, fh)
        d = m.event_title_read_data(alive=lambda: False, path=p, default="Default Cup")
        assert d["title"] == "From File" and d["source"] == "file", d


def t_event_title_write_posts_to_relay_when_alive():
    sent = {}
    def post(url, payload):
        sent["url"] = url; sent["payload"] = payload
        return {"ok": True, "title": payload["title"]}
    d = m.event_title_write_data("  Round 5  ", alive=lambda: True, post=post,
                                 sanitize=lambda s: s.strip())
    assert d == {"ok": True, "title": "Round 5", "applied": "relay"}, d
    assert sent["url"].endswith("/event/title"), sent
    assert sent["payload"] == {"title": "Round 5"}, sent


def t_event_title_write_writes_file_when_relay_down():
    import json, tempfile
    with tempfile.TemporaryDirectory() as dd:
        p = os.path.join(dd, "sub", "event.json")     # dir created on demand
        d = m.event_title_write_data("Round 6", alive=lambda: False, path=p,
                                     sanitize=lambda s: s.strip())
        assert d == {"ok": True, "title": "Round 6", "applied": "file"}, d
        with open(p, encoding="utf-8") as fh:
            assert json.load(fh) == {"title": "Round 6"}


def t_event_title_write_applies_the_real_relay_sanitizer():
    # No sanitize seam -> exercises _event_title_sanitizer loading the relay rule.
    import tempfile
    with tempfile.TemporaryDirectory() as dd:
        p = os.path.join(dd, "event.json")
        d = m.event_title_write_data("Round\n7\tCup", alive=lambda: False, path=p)
        assert d["ok"] and d["title"] == "Round7Cup", d   # control chars stripped


def t_event_title_write_relay_error_returns_not_ok():
    def boom(_u, _p):
        raise OSError("connection refused")
    d = m.event_title_write_data("x", alive=lambda: True, post=boom,
                                 sanitize=lambda s: s)
    assert d["ok"] is False and "connection refused" in d["error"], d


def t_console_internal_host_prefers_tailscale_then_loopback():
    # The Control Center's "internal" cockpit link mirrors the relay panel:
    # the producer's Tailscale IP when the tailnet is up, 127.0.0.1 when down.
    assert m._console_internal_host("100.64.0.5") == "100.64.0.5"
    assert m._console_internal_host(None) == "127.0.0.1"
    assert m._console_internal_host("") == "127.0.0.1"


def _with_cockpit_secret_env_cleared(fn):
    """Run fn() with RACECAST_CONSOLE_SECRET cleared +
    _active_profile_env_strict restored afterwards (zero-config auto-provision tests
    monkeypatch both)."""
    saved_new = os.environ.pop("RACECAST_CONSOLE_SECRET", None)
    saved_strict = m._active_profile_env_strict
    try:
        fn()
    finally:
        m._active_profile_env_strict = saved_strict
        os.environ.pop("RACECAST_CONSOLE_SECRET", None)
        if saved_new is not None:
            os.environ["RACECAST_CONSOLE_SECRET"] = saved_new


def t_ensure_active_console_secret_generates_and_is_idempotent():
    import tempfile
    def body():
        with tempfile.TemporaryDirectory() as dd:
            ppath = os.path.join(dd, "profile.env")
            with open(ppath, "w", encoding="utf-8") as fh:
                fh.write("NAME=Demo\n")
            m._active_profile_env_strict = lambda: ("demo", ppath)
            s1 = m._ensure_active_console_secret()
            assert s1 and len(s1) == 64, s1                  # token_hex(32) -> 64 hex
            assert os.environ.get("RACECAST_CONSOLE_SECRET") == s1   # new env var name
            with open(ppath, encoding="utf-8") as fh:
                written = m.parse_env_text(fh.read())
            assert written.get("CONSOLE_SECRET") == s1        # new key written
            os.environ.pop("RACECAST_CONSOLE_SECRET", None)   # idempotent: returns the same
            assert m._ensure_active_console_secret() == s1
            with open(ppath, encoding="utf-8") as fh:
                assert m.parse_env_text(fh.read())["NAME"] == "Demo"   # other keys kept
    _with_cockpit_secret_env_cleared(body)


def t_ensure_active_console_secret_skips_example_and_missing():
    import tempfile
    def body():
        with tempfile.TemporaryDirectory() as dd:
            ppath = os.path.join(dd, "profile.env")
            with open(ppath, "w", encoding="utf-8") as fh:
                fh.write("NAME=Example\n")
            m._active_profile_env_strict = lambda: ("example", ppath)   # never the shipped profile
            assert m._ensure_active_console_secret() is None
            m._active_profile_env_strict = lambda: ("ghost",
                                                    os.path.join(dd, "nope", "profile.env"))
            assert m._ensure_active_console_secret() is None            # never fabricate a profile
    _with_cockpit_secret_env_cleared(body)


def t_ensure_active_console_secret_respects_existing_env():
    """RACECAST_CONSOLE_SECRET in env is returned immediately without resolving a profile."""
    def body():
        os.environ["RACECAST_CONSOLE_SECRET"] = "already-set"
        def _boom():
            raise AssertionError("must not resolve a profile when env carries a secret")
        m._active_profile_env_strict = _boom
        assert m._ensure_active_console_secret() == "already-set"
    _with_cockpit_secret_env_cleared(body)



def t_log_sources_registry_shape():
    src = m._log_sources()
    assert set(["relay", "streams", "obs", "companion", "tailscale",
                "aggregate", "app"]) <= set(src)
    for _name, spec in src.items():
        assert callable(spec["files"])          # () -> list[path]
        assert callable(spec["archives"])       # () -> list[token]
        assert callable(spec["read"])           # (token) -> text
    # the app source points at the machine-wide runtime/logs dir
    assert src["app"]["dir"] == m._ui_app_log_dir()
    # aggregate's file set is the union of the FIVE service sources (app is standalone)
    agg = set(src["aggregate"]["files"]())
    parts = set()
    for n in ("relay", "streams", "obs", "companion", "tailscale"):
        parts |= set(src[n]["files"]())
    assert agg == parts


def t_dispatch_has_obs_and_tailscale_logs():
    assert ("obs", "logs") in m.DISPATCH
    assert ("tailscale", "logs") in m.DISPATCH


def t_relay_start_spawns_to_boot_log_not_console():
    # The relay daemon attaches its own rotating handler to relay.console.log; the
    # detached spawn MUST capture raw stdout/stderr to a separate boot file, or the
    # two writers corrupt midnight rotation (the inherited fd keeps writing to the
    # renamed inode).
    import inspect
    # The spawn and its retry live in _spawn_relay_verified, so the guard does too.
    src = inspect.getsource(m._spawn_relay_verified)
    assert "_relay_boot_log_path()" in src
    assert "_relay_log_path()" not in src   # never hand the console log to start_detached
    assert m._relay_boot_log_path() != m._relay_log_path()


def t_ui_app_log_path_is_machine_wide():
    # app.log lives at the un-scoped runtime base, NOT under a profile.
    assert m._ui_app_log_dir() == os.path.join(m._runtime_base_dir(), "logs")
    assert m._ui_app_log_path() == os.path.join(m._runtime_base_dir(), "logs", "app.log")


def t_run_ui_wires_app_logger():
    import inspect
    src = inspect.getsource(m.run_ui)
    assert 'configure_logging("racecast.app"' in src   # dedicated logger created
    assert "_ui_app_log_path()" in src
    assert "prune_old_logs" in src                      # retention applied at startup
    assert "logger=_app_logger" in src                  # passed into the JobManager


def t_console_status_links_union_crew():
    # console_status_data() must union _crew_roster_safe() into the link list,
    # deduped by streamer_key. Both rosters contribute; dedup removes same-key dupes.
    orig_sched = m._console_roster_safe
    orig_crew = m._crew_roster_safe
    orig_secret = m._ensure_active_console_secret
    orig_magic = m._tailscale_magicdns
    try:
        m._console_roster_safe = lambda: ["Alice"]
        m._crew_roster_safe = lambda: ["Dana the Director"]
        m._ensure_active_console_secret = lambda: "s" * 64
        m._tailscale_magicdns = lambda: "host.tail.ts.net"
        data = m.console_status_data()
        names = [l["name"] for l in data["links"]]
        assert names == ["Alice", "Dana the Director"], names
        assert all("/console?t=" in l["internal"] for l in data["links"])
        # the shared (token-free) landing-page link the distribute buttons use
        assert data["console_url"] == "https://host.tail.ts.net/console"
    finally:
        m._console_roster_safe = orig_sched
        m._crew_roster_safe = orig_crew
        m._ensure_active_console_secret = orig_secret
        m._tailscale_magicdns = orig_magic


def t_console_status_console_url_empty_without_magicdns():
    orig_secret = m._ensure_active_console_secret
    orig_magic = m._tailscale_magicdns
    try:
        m._ensure_active_console_secret = lambda: "s" * 64
        m._tailscale_magicdns = lambda: ""
        assert m.console_status_data()["console_url"] == ""
    finally:
        m._ensure_active_console_secret = orig_secret
        m._tailscale_magicdns = orig_magic


def t_console_post_link_errors_without_magicdns():
    orig = m._tailscale_magicdns
    try:
        m._tailscale_magicdns = lambda: ""
        r = m.console_post_link_data()
        assert r["ok"] is False and "MagicDNS" in r["error"]
    finally:
        m._tailscale_magicdns = orig


def t_console_post_link_errors_without_webhook():
    orig_magic = m._tailscale_magicdns
    orig_hook = m._active_discord_webhook
    try:
        m._tailscale_magicdns = lambda: "h.ts.net"
        m._active_discord_webhook = lambda: ("", "")
        r = m.console_post_link_data()
        assert r["ok"] is False and "DISCORD_WEBHOOK_URL" in r["error"]
    finally:
        m._tailscale_magicdns = orig_magic
        m._active_discord_webhook = orig_hook


def t_console_post_link_posts_payload_to_webhook():
    sent = {}
    orig_magic = m._tailscale_magicdns
    orig_hook = m._active_discord_webhook
    orig_post = m._post_discord_webhook
    try:
        m._tailscale_magicdns = lambda: "h.ts.net"
        m._active_discord_webhook = lambda: ("https://discord/webhook", "GT Masters")
        m._post_discord_webhook = lambda url, payload: sent.update(url=url, payload=payload)
        r = m.console_post_link_data()
        assert r["ok"] is True
        assert sent["url"] == "https://discord/webhook"
        assert sent["payload"]["content"] == "@here"
        # the server-computed link rides in the embed (built from MagicDNS)
        assert "https://h.ts.net/console" in sent["payload"]["embeds"][0]["description"]
        assert sent["payload"]["embeds"][0]["footer"] == {"text": "GT Masters"}
    finally:
        m._tailscale_magicdns = orig_magic
        m._active_discord_webhook = orig_hook
        m._post_discord_webhook = orig_post


def t_console_post_link_reports_post_failure():
    orig_magic = m._tailscale_magicdns
    orig_hook = m._active_discord_webhook
    orig_post = m._post_discord_webhook
    def boom(url, payload):
        raise RuntimeError("HTTP 404")
    try:
        m._tailscale_magicdns = lambda: "h.ts.net"
        m._active_discord_webhook = lambda: ("https://discord/webhook", "")
        m._post_discord_webhook = boom
        r = m.console_post_link_data()
        assert r["ok"] is False and "404" in r["error"]
    finally:
        m._tailscale_magicdns = orig_magic
        m._active_discord_webhook = orig_hook
        m._post_discord_webhook = orig_post


def t_post_discord_webhook_sets_user_agent():
    # Discord sits behind Cloudflare, which 403s the default urllib
    # "Python-urllib/x.y" User-Agent. The poster MUST send an explicit UA or the
    # link never arrives. Capture the Request instead of hitting the network, and
    # patch http_util.urlopen, the name bound at import time in the helper module
    # (urllib.request.urlopen is a different binding after the http_util migration).
    import http_util as _hu
    captured = {}

    class _Resp:
        def read(self):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=None):
        captured["ua"] = req.get_header("User-agent")
        captured["ct"] = req.get_header("Content-type")
        return _Resp()

    orig = _hu.urlopen
    _hu.urlopen = fake_urlopen
    try:
        m._post_discord_webhook("https://discord/webhook", {"content": "hi"})
    finally:
        _hu.urlopen = orig
    assert captured["ua"] and "Python-urllib" not in captured["ua"], captured["ua"]
    assert captured["ct"] == "application/json", captured["ct"]


def t_crew_entries_data_maps_relay_rows():
    seen = {}
    def fake_fetch(url, timeout=3):
        seen["url"] = url
        return {"rows": [
            {"name": "Dana", "director": True, "producer": False,
             "commentator": False, "discord": "dana_d"},
            {"name": "Pia", "director": 0, "producer": "x",
             "commentator": "x", "race_control": "x", "discord": None}]}
    orig = m._relay_fetch_json
    m._relay_fetch_json = fake_fetch
    try:
        out = m.crew_entries_data()
    finally:
        m._relay_fetch_json = orig
    assert out["ok"] is True, out
    # Each entry carries a 1-based crew DATA-row index so the editor can Save/Delete
    # a sheet-loaded row. /crew/data is index-free, so without it the UI sends
    # row=undefined and every edit of an existing person fails.
    assert out["entries"] == [
        {"row": 1, "name": "Dana", "director": True, "producer": False,
         "commentator": False, "race_control": False, "discord": "dana_d"},
        {"row": 2, "name": "Pia", "director": False, "producer": True,
         "commentator": True, "race_control": True, "discord": ""}]
    assert [e["row"] for e in out["entries"]] == [1, 2]
    assert seen["url"].endswith("/crew/data")


def t_crew_entries_data_relay_down_is_error_not_raise():
    def boom(url, timeout=3):
        raise OSError("connection refused")
    orig = m._relay_fetch_json
    m._relay_fetch_json = boom
    try:
        out = m.crew_entries_data()
    finally:
        m._relay_fetch_json = orig
    assert out["ok"] is False and "error" in out


def t_crew_write_and_delete_post_to_relay():
    posts = []
    def fake_post(url, payload, timeout=3):
        posts.append((url, payload))
        return {"ok": True, "row": payload.get("row")}
    orig = m._relay_post_json
    m._relay_post_json = fake_post
    try:
        w = m.crew_write_data(2, "Dana", True, False, commentator=True,
                              race_control=True, discord="  dana_d ")
        d = m.crew_delete_data(3)
    finally:
        m._relay_post_json = orig
    assert w == {"ok": True, "row": 2}
    assert posts[0][0].endswith("/crew/set")
    assert posts[0][1] == {"row": 2, "name": "Dana", "director": True, "producer": False,
                           "commentator": True, "race_control": True, "discord": "dana_d"}
    assert d == {"ok": True, "row": 3}
    assert posts[1][0].endswith("/crew/delete") and posts[1][1] == {"row": 3}


# The singleton relay control port and the profile-switch guard. (#273)

def t_relay_pid_is_singleton_top_level():
    # The relay binds the SHARED control port (8088) + feed ports, so only ONE
    # can run per machine. Its PID lives at the un-scoped runtime/ top level, NOT
    # under runtime/<profile>/, so stop/status find the one relay regardless of the
    # active profile. (#273)
    orig_base, orig_active = m._runtime_base_dir, m._active_profile_name
    base = os.path.join("X", "runtime")
    m._runtime_base_dir = lambda: base
    m._active_profile_name = lambda: "league-a"
    try:
        assert m._relay_pid_path() == os.path.join(base, "relay.pid")
        assert "league-a" not in m._relay_pid_path()
        assert m._relay_profile_path() == os.path.join(base, "relay.profile")
    finally:
        m._runtime_base_dir, m._active_profile_name = orig_base, orig_active


def t_running_relay_dir_follows_profile_stamp():
    import tempfile
    td = tempfile.mkdtemp()
    base = os.path.join(td, "runtime")
    os.makedirs(base)
    orig_base, orig_active = m._runtime_base_dir, m._active_profile_name
    m._runtime_base_dir = lambda: base
    m._active_profile_name = lambda: "league-b"        # active switched away
    try:
        # no stamp -> falls back to the ACTIVE profile's dir
        assert m._running_relay_dir() == os.path.join(base, "league-b")
        # stamp present -> the relay's OWN profile dir, regardless of active, so
        # `relay logs`/`status` read the dir the daemon actually writes to.
        with open(m._relay_profile_path(), "w", encoding="utf-8") as fh:
            fh.write("league-a")
        assert m._running_relay_dir() == os.path.join(base, "league-a")
        assert m._relay_log_path() == \
            os.path.join(base, "league-a", "logs", "relay.console.log")
    finally:
        m._runtime_base_dir, m._active_profile_name = orig_base, orig_active


def t_relay_start_plan_port_free_starts():
    action, kill, reason = m.relay_start_plan(
        port_pids=[], feed_pids=[], pidfile_pid=None, pidfile_alive=False,
        running_profile="", active_profile="testing", http_ok=False)
    assert action == "start" and kill == [] and reason == ""


def t_relay_start_plan_healthy_active_is_noop():
    action, kill, reason = m.relay_start_plan(
        port_pids=[100], feed_pids=[200], pidfile_pid=100, pidfile_alive=True,
        running_profile="testing", active_profile="testing", http_ok=True)
    assert action == "running" and kill == [] and reason == ""


def t_relay_start_plan_dead_pidfile_but_port_held_heals():
    action, kill, reason = m.relay_start_plan(
        port_pids=[100], feed_pids=[], pidfile_pid=None, pidfile_alive=False,
        running_profile="", active_profile="testing", http_ok=False)
    assert action == "heal" and kill == [100]
    assert "dead pidfile" in reason and "100" in reason


def t_relay_start_plan_split_brain_heals_and_unions_pids():
    action, kill, reason = m.relay_start_plan(
        port_pids=[100, 101], feed_pids=[200], pidfile_pid=100, pidfile_alive=True,
        running_profile="testing", active_profile="testing", http_ok=True)
    assert action == "heal" and kill == [100, 101, 200] and "split-brain" in reason


def t_relay_start_plan_not_responding_heals():
    action, kill, reason = m.relay_start_plan(
        port_pids=[100], feed_pids=[], pidfile_pid=100, pidfile_alive=True,
        running_profile="testing", active_profile="testing", http_ok=False)
    assert action == "heal" and kill == [100] and "responding" in reason


def t_relay_start_plan_foreign_holder_heals():
    action, kill, reason = m.relay_start_plan(
        port_pids=[999], feed_pids=[], pidfile_pid=100, pidfile_alive=True,
        running_profile="testing", active_profile="testing", http_ok=True)
    assert action == "heal" and kill == [999] and "foreign" in reason


def t_relay_start_plan_wrong_profile_heals_and_names_both():
    action, kill, reason = m.relay_start_plan(
        port_pids=[100], feed_pids=[], pidfile_pid=100, pidfile_alive=True,
        running_profile="iro-gtec", active_profile="testing", http_ok=True)
    assert action == "heal" and kill == [100]
    assert "iro-gtec" in reason and "testing" in reason


def t_relay_start_plan_empty_stamp_is_mismatch_heals():
    # A current-binary relay always stamps its profile; an absent stamp means an
    # old/pre-stamp daemon -> heal (never a "running" no-op on an empty stamp).
    action, kill, reason = m.relay_start_plan(
        port_pids=[100], feed_pids=[], pidfile_pid=100, pidfile_alive=True,
        running_profile="", active_profile="testing", http_ok=True)
    assert action == "heal" and kill == [100]
    assert "(none)" in reason and "testing" in reason


def t_profile_switch_block_reason():
    assert m.profile_switch_block_reason(False, False, False) == []
    assert m.profile_switch_block_reason(True, False, False) == ["relay"]
    assert m.profile_switch_block_reason(False, True, False) == ["static streams"]
    assert m.profile_switch_block_reason(True, True, False) == ["relay", "static streams"]
    assert m.profile_switch_block_reason(True, True, True) == []   # --force overrides


def t_route_health_subcommand():
    assert m.route(["health", "export"]) == {"kind": "health", "rest": ["export"]}
    assert m.route(["health", "pull", "100.64.0.1"]) == {"kind": "health", "rest": ["pull", "100.64.0.1"]}
    # bare "health" (no verb) routes correctly; health_cmd handles the usage error
    assert m.route(["health"]) == {"kind": "health", "rest": []}


def t_health_export_import_roundtrip():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "health-history.db")
        conn = m.hsmod.open_db(db)
        m.hsmod.migrate(conn)
        m.hsmod.record(conn, {"ts": 1.0, "health_level": "green", "health_reasons": []}, "periodic")
        conn.close()
        out = os.path.join(d, "dump.jsonl")
        # Init the second DB up front and CLOSE it: an open sqlite handle blocks
        # tempdir cleanup (rmtree) on Windows (WinError 32).
        db2 = os.path.join(d, "h2.db")
        c2 = m.hsmod.open_db(db2); m.hsmod.migrate(c2); c2.close()
        # Point the CLI at our temp DB via monkeypatching the path resolver.
        orig_path = m._health_db_path
        try:
            m._health_db_path = lambda: db
            m.health_cmd(["export", "--out", out])
            assert os.path.exists(out) and os.path.getsize(out) > 0
            # Import into the second DB.
            m._health_db_path = lambda: db2
            m.health_cmd(["import", out])
            conn2 = m.hsmod.open_db(db2)
            try:
                assert len(m.hsmod.query_range(conn2, 0, 1e12)) == 1
            finally:
                conn2.close()
        finally:
            m._health_db_path = orig_path


# producer_schedule_data, a tolerant provider with seams.
_PRODUCER_CSV = ("Part,Producer,MagicDNS\r\n"
                 "1,Alice,producer-a.tail1234.ts.net\r\n"
                 "2,Bob,producer-b.tail1234.ts.net\r\n")


def t_producer_schedule_tags_self_and_shape():
    os.environ["RACECAST_SHEET_ID"] = "SHEET123"
    data = m.producer_schedule_data(
        fetch=lambda url: _PRODUCER_CSV,
        self_name="producer-b.tail1234.ts.net",
        refresh_env=lambda: None)
    assert data["self_name"] == "producer-b.tail1234.ts.net"
    assert data["self_known"] is True
    rows = data["rows"]
    assert [r["producer"] for r in rows] == ["Alice", "Bob"]
    assert rows[0]["self"] is False
    assert rows[1]["self"] is True           # Bob == me -> locked


def t_producer_schedule_unknown_self_known_false():
    os.environ["RACECAST_SHEET_ID"] = "SHEET123"
    data = m.producer_schedule_data(
        fetch=lambda url: _PRODUCER_CSV, self_name="", refresh_env=lambda: None)
    assert data["self_known"] is False
    assert all(r["self"] is False for r in data["rows"])


def t_producer_schedule_no_sheet_id_is_empty():
    os.environ.pop("RACECAST_SHEET_ID", None)
    data = m.producer_schedule_data(
        fetch=lambda url: _PRODUCER_CSV, self_name="x.ts.net", refresh_env=lambda: None)
    assert data == {"rows": [], "self_name": "x.ts.net", "self_known": True}


def t_producer_schedule_fetch_failure_is_empty():
    os.environ["RACECAST_SHEET_ID"] = "SHEET123"
    def boom(url):
        raise OSError("network down")
    data = m.producer_schedule_data(
        fetch=boom, self_name="x.ts.net", refresh_env=lambda: None)
    assert data["rows"] == [] and data["self_known"] is True


def t_producer_schedule_pins_gviz_header_row():
    # gviz auto-detection merges the header into row 1 when the Part column mixes
    # text + numbers (an empty/"Q" qualifying label alongside numbered parts), so
    # parse_producer_rows finds no header and returns [] -> the Home "Producer
    # schedule" card AND the relay Parts control silently come up empty (the
    # stream keys never resolve). The fetch MUST pin headers=1 so gviz always
    # treats sheet row 1 as the header.
    os.environ["RACECAST_SHEET_ID"] = "SHEET123"
    seen = {}
    def capture(url):
        seen["url"] = url
        return _PRODUCER_CSV
    m.producer_schedule_data(
        fetch=capture, self_name="x.ts.net", refresh_env=lambda: None)
    assert "headers=1" in seen["url"], seen["url"]


# Producer identity for events. (#317)
def t_takeover_producer_extracts_name():
    assert m._takeover_producer({"producer": "Alice"}) == "Alice"
    assert m._takeover_producer({"producer": "  Bob  "}) == "Bob"
    assert m._takeover_producer({}) == ""
    assert m._takeover_producer(None) == ""
    assert m._takeover_producer({"producer": None}) == ""


def t_resolve_producer_name_prefers_sheet_self_row():
    m._PRODUCER_NAME_CACHE = None
    orig = m.producer_schedule_data
    try:
        m.producer_schedule_data = lambda: {"rows": [
            {"producer": "Alice", "self": False},
            {"producer": "Bob", "self": True}]}
        assert m._resolve_producer_name() == "Bob"
    finally:
        m.producer_schedule_data = orig
        m._PRODUCER_NAME_CACHE = None


def t_resolve_producer_name_env_then_hostname_fallback():
    import socket
    m._PRODUCER_NAME_CACHE = None
    orig = m.producer_schedule_data
    try:
        m.producer_schedule_data = lambda: {"rows": [{"producer": "Alice", "self": False}]}
        os.environ["RACECAST_PRODUCER_NAME"] = "Manual Override"
        assert m._resolve_producer_name() == "Manual Override"   # no self row -> env wins
        m._PRODUCER_NAME_CACHE = None
        os.environ.pop("RACECAST_PRODUCER_NAME", None)
        assert m._resolve_producer_name() == socket.gethostname()  # then hostname
    finally:
        m.producer_schedule_data = orig
        os.environ.pop("RACECAST_PRODUCER_NAME", None)
        m._PRODUCER_NAME_CACHE = None


def t_announce_takeover_writes_health_event_hermetic():
    import tempfile
    orig_resolve = m._resolve_producer_name
    orig_wh = m._active_discord_webhook
    orig_dbpath = m._health_db_path
    with tempfile.TemporaryDirectory() as d:
        dbp = os.path.join(d, "h.db")
        m._resolve_producer_name = lambda: "Mara"          # no sheet fetch
        m._active_discord_webhook = lambda: ("", "")        # no webhook -> no Discord post
        m._health_db_path = lambda: dbp                     # temp DB, never the real runtime
        try:
            _ORIG_ANNOUNCE_TAKEOVER({"producer": "Jens"},
                                    {"stint": 7, "qualifying": False}, "GTEC R4")
            conn = m.hsmod.open_db(dbp)
            rows = m.hsmod.query_events(conn, 0, 1e12)
            conn.close()
            assert len(rows) == 1 and rows[0]["type"] == "takeover"
            assert rows[0]["producer"] == "Mara"
            assert rows[0]["label"] == "Mara took over from Jens"
            assert rows[0]["metadata"] == {"from": "Jens", "stint": 7}
        finally:
            m._resolve_producer_name = orig_resolve
            m._active_discord_webhook = orig_wh
            m._health_db_path = orig_dbpath


def t_resolve_producer_name_tolerates_fetch_failure():
    m._PRODUCER_NAME_CACHE = None
    orig = m.producer_schedule_data
    try:
        def boom():
            raise OSError("network down")
        m.producer_schedule_data = boom
        os.environ["RACECAST_PRODUCER_NAME"] = "Fallback"
        assert m._resolve_producer_name() == "Fallback"
    finally:
        m.producer_schedule_data = orig
        os.environ.pop("RACECAST_PRODUCER_NAME", None)
        m._PRODUCER_NAME_CACHE = None


def t_route_obs_stream_target_is_accepted():
    action = m.route(["obs", "stream-target", "1"])
    assert action["command"] == "obs" and action["verb"] == "stream-target"
    assert action["rest"] == ["1"]


def t_apply_stream_target_happy_path_sets_service_and_hides_key():
    # Seams: fetch(url)->csv text by tab, post(url,obj)->webhook bytes,
    # apply_obs(platform,key)->(ok,note). refresh_env no-op.
    producer_csv = "Part,Producer,MagicDNS,Stream Key\r\n1,Alice,a.ts.net,key1\r\n"
    channel_csv = "Platform,Channel\r\ntwitch,https://twitch.tv/foo\r\n"

    def fetch(url):
        return producer_csv if "Producer" in url else channel_csv

    def post(url, obj):
        assert obj == {"action": "get_stream_key", "ref": "key1"}
        return b'{"ok": true, "action": "get_stream_key", "key": "SECRET"}'

    seen = {}
    def apply_obs(platform, key):
        seen["platform"], seen["key"] = platform, key
        return True, ""

    _saved_id = os.environ.get("RACECAST_SHEET_ID")
    _saved_push = os.environ.get("RACECAST_SHEET_PUSH_URL")
    try:
        os.environ["RACECAST_SHEET_ID"] = "SID"
        os.environ["RACECAST_SHEET_PUSH_URL"] = "https://script.example/exec"
        ok, note = m._apply_stream_target("1", fetch=fetch, post=post,
                                          apply_obs=apply_obs, refresh_env=lambda: None)
        assert ok is True, note
        assert seen == {"platform": "twitch", "key": "SECRET"}
        assert "SECRET" not in note                       # key never surfaced
    finally:
        if _saved_id is None:
            os.environ.pop("RACECAST_SHEET_ID", None)
        else:
            os.environ["RACECAST_SHEET_ID"] = _saved_id
        if _saved_push is None:
            os.environ.pop("RACECAST_SHEET_PUSH_URL", None)
        else:
            os.environ["RACECAST_SHEET_PUSH_URL"] = _saved_push


def t_apply_stream_target_no_ref_is_clear_error():
    producer_csv = "Part,Producer,MagicDNS,Stream Key\r\n1,Alice,a.ts.net,\r\n"
    channel_csv = "Platform,Channel\r\ntwitch,x\r\n"
    _saved_id = os.environ.get("RACECAST_SHEET_ID")
    _saved_push = os.environ.get("RACECAST_SHEET_PUSH_URL")
    try:
        os.environ["RACECAST_SHEET_ID"] = "SID"
        os.environ["RACECAST_SHEET_PUSH_URL"] = "https://script.example/exec"
        ok, note = m._apply_stream_target(
            "1", fetch=lambda u: producer_csv if "Producer" in u else channel_csv,
            post=lambda u, o: b"{}", apply_obs=lambda p, k: (True, ""),
            refresh_env=lambda: None)
        assert ok is False and "reference" in note.lower()
    finally:
        if _saved_id is None:
            os.environ.pop("RACECAST_SHEET_ID", None)
        else:
            os.environ["RACECAST_SHEET_ID"] = _saved_id
        if _saved_push is None:
            os.environ.pop("RACECAST_SHEET_PUSH_URL", None)
        else:
            os.environ["RACECAST_SHEET_PUSH_URL"] = _saved_push


def t_apply_stream_target_webhook_error_surfaces_and_skips_obs():
    producer_csv = "Part,Producer,MagicDNS,Stream Key\r\n1,Alice,a.ts.net,key1\r\n"
    channel_csv = "Platform,Channel\r\ntwitch,x\r\n"
    _saved_id = os.environ.get("RACECAST_SHEET_ID")
    _saved_push = os.environ.get("RACECAST_SHEET_PUSH_URL")
    called = {"obs": 0}
    def apply_obs(p, k):
        called["obs"] += 1; return True, ""
    try:
        os.environ["RACECAST_SHEET_ID"] = "SID"
        os.environ["RACECAST_SHEET_PUSH_URL"] = "https://script.example/exec"
        ok, note = m._apply_stream_target(
            "1", fetch=lambda u: producer_csv if "Producer" in u else channel_csv,
            post=lambda u, o: b'{"ok": false, "error": "no key for ref \'key1\'"}',
            apply_obs=apply_obs, refresh_env=lambda: None)
        assert ok is False and "no key" in note
        assert called["obs"] == 0                          # never touched OBS
    finally:
        if _saved_id is None:
            os.environ.pop("RACECAST_SHEET_ID", None)
        else:
            os.environ["RACECAST_SHEET_ID"] = _saved_id
        if _saved_push is None:
            os.environ.pop("RACECAST_SHEET_PUSH_URL", None)
        else:
            os.environ["RACECAST_SHEET_PUSH_URL"] = _saved_push


def t_part_index_default_and_parse():
    assert m._part_index([]) == 1
    assert m._part_index(["--part", "2"]) == 2
    assert m._part_index(["--part=3"]) == 3


def t_part_index_rejects_bad():
    for bad in (["--part", "0"], ["--part", "x"], ["--part=-1"]):
        try:
            m._part_index(bad)
            raise AssertionError("expected SystemExit for {!r}".format(bad))
        except SystemExit:
            pass


def t_write_part_reset_writes_file():
    import json as _json, tempfile as _tf
    d = _tf.mkdtemp()
    orig = m._runtime_dir
    m._runtime_dir = lambda: d
    try:
        m._write_part_reset(2)
        with open(m._part_path(), encoding="utf-8") as fh:
            assert _json.load(fh) == {"index": 2, "live": False}
    finally:
        m._runtime_dir = orig


def t_discord_autojoin_gate():
    assert m._discord_autojoin_enabled({}) is True                       # default on
    assert m._discord_autojoin_enabled({"RACECAST_DISCORD_AUTOJOIN": "0"}) is False
    assert m._discord_autojoin_enabled({"RACECAST_DISCORD_AUTOJOIN": "1"}) is True


def t_collection_switch_enabled_default_on_and_optout():
    orig = m._machine_env_value
    try:
        m._machine_env_value = lambda name: ""                 # unset -> default on
        assert m._collection_switch_enabled() is True
        for off in ("0", "false", "no", "off", " OFF ", "False"):
            m._machine_env_value = lambda name, v=off: v
            assert m._collection_switch_enabled() is False, off
        for on in ("1", "true", "yes", "on", "anything"):
            m._machine_env_value = lambda name, v=on: v
            assert m._collection_switch_enabled() is True, on
    finally:
        m._machine_env_value = orig


def t_standby_on_start_enabled_default_on_and_optout():
    orig = m._machine_env_value
    try:
        m._machine_env_value = lambda name: ""                 # unset -> default on
        assert m._standby_on_start_enabled() is True
        for off in ("0", "false", "no", "off", " OFF ", "False"):
            m._machine_env_value = lambda name, v=off: v
            assert m._standby_on_start_enabled() is False, off
        for on in ("1", "true", "yes", "on", "anything"):
            m._machine_env_value = lambda name, v=on: v
            assert m._standby_on_start_enabled() is True, on
    finally:
        m._machine_env_value = orig


def _obsws_module():
    import sys as _sys
    SCRIPTS = os.path.join(ROOT, "src", "scripts")
    if SCRIPTS not in _sys.path:
        _sys.path.insert(0, SCRIPTS)
    import obs_ws
    return obs_ws


def t_check_scene_collection_switches_on_mismatch_when_enabled():
    import io, contextlib
    obs_ws = _obsws_module()
    expected = "GT Racing Endurance — demo"
    st = obs_ws.scene_collection_status(
        "Old League", ["Old League", expected], expected=expected)   # mismatch, present
    calls = {}
    saved = (obs_ws.get_scene_collection, obs_ws.set_scene_collection,
             m._active_obs_collection, m._collection_switch_enabled)
    try:
        obs_ws.get_scene_collection = lambda **kw: (st, "")
        def _fake_set(name, **kw):
            calls["name"] = name
            return (True, "")
        obs_ws.set_scene_collection = _fake_set
        m._active_obs_collection = lambda: expected
        m._collection_switch_enabled = lambda: True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m._check_scene_collection()
        assert calls.get("name") == expected, calls
        assert "switched to" in buf.getvalue(), buf.getvalue()
    finally:
        (obs_ws.get_scene_collection, obs_ws.set_scene_collection,
         m._active_obs_collection, m._collection_switch_enabled) = saved


def t_check_scene_collection_warns_not_switches_when_disabled():
    import io, contextlib
    obs_ws = _obsws_module()
    expected = "GT Racing Endurance — demo"
    st = obs_ws.scene_collection_status(
        "Old League", ["Old League", expected], expected=expected)
    calls = {}
    saved = (obs_ws.get_scene_collection, obs_ws.set_scene_collection,
             m._active_obs_collection, m._collection_switch_enabled)
    try:
        obs_ws.get_scene_collection = lambda **kw: (st, "")
        def _fake_set(name, **kw):
            calls["name"] = name
            return (True, "")
        obs_ws.set_scene_collection = _fake_set
        m._active_obs_collection = lambda: expected
        m._collection_switch_enabled = lambda: False              # kill-switch
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m._check_scene_collection()
        assert "name" not in calls, "must not switch when disabled"
        out = buf.getvalue()
        assert "WARNING" in out and expected in out, out
    finally:
        (obs_ws.get_scene_collection, obs_ws.set_scene_collection,
         m._active_obs_collection, m._collection_switch_enabled) = saved


def t_check_scene_collection_warns_when_switch_fails():
    import io, contextlib
    obs_ws = _obsws_module()
    expected = "GT Racing Endurance — demo"
    st = obs_ws.scene_collection_status(
        "Old League", ["Old League", expected], expected=expected)   # mismatch, present
    calls = {}
    saved = (obs_ws.get_scene_collection, obs_ws.set_scene_collection,
             m._active_obs_collection, m._collection_switch_enabled)
    try:
        obs_ws.get_scene_collection = lambda **kw: (st, "")
        def _fake_set(name, **kw):
            calls["name"] = name
            return (False, "output active")
        obs_ws.set_scene_collection = _fake_set
        m._active_obs_collection = lambda: expected
        m._collection_switch_enabled = lambda: True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m._check_scene_collection()
        assert calls.get("name") == expected, calls
        out = buf.getvalue()
        assert "Could not switch" in out and "output active" in out, out
    finally:
        (obs_ws.get_scene_collection, obs_ws.set_scene_collection,
         m._active_obs_collection, m._collection_switch_enabled) = saved


def t_event_stop_reports_then_tears_down():
    calls = []
    saved = (m._build_report_file, m._send_report_core, m.relay_stop,
              m.companion_stop, m.streams_stop, m._streams_static_dir,
              m._relay_is_alive)
    try:
        m._build_report_file = lambda: (calls.append("build"),
                                         {"path": "/tmp/r.html", "summary": "s",
                                          "report": {"x": 1}})[1]
        m._send_report_core = lambda p, report=None, window=None: calls.append("send")
        m.relay_stop = lambda a: calls.append("relay_stop")
        m.companion_stop = lambda a: calls.append("companion_stop")
        m.streams_stop = lambda a: calls.append("streams_stop")
        m._streams_static_dir = lambda: "/nonexistent-streams-dir"   # no feed pids
        m._relay_is_alive = lambda: True                 # a live event to stop

        m.event_stop([])
        assert calls.index("send") < calls.index("relay_stop")   # report BEFORE teardown
        assert "build" in calls and "relay_stop" in calls
        # relay is stopped LAST: on Windows the spawned `event stop` is a child of
        # the relay and relay_stop's `taskkill /T` would kill it mid-teardown, so
        # companion/streams cleanup must finish first.
        assert calls.index("companion_stop") < calls.index("relay_stop")

        calls.clear()
        m.event_stop(["--no-report"])
        assert "build" not in calls and "send" not in calls
        assert "relay_stop" in calls
    finally:
        (m._build_report_file, m._send_report_core, m.relay_stop,
         m.companion_stop, m.streams_stop, m._streams_static_dir,
         m._relay_is_alive) = saved


def t_event_stop_report_failure_still_tears_down():
    calls = []
    saved = (m._build_report_file, m.relay_stop, m.companion_stop,
              m.streams_stop, m._streams_static_dir, m._relay_is_alive)
    try:
        def boom():
            raise RuntimeError("no health data")
        m._build_report_file = boom
        m.relay_stop = lambda a: calls.append("relay_stop")
        m.companion_stop = lambda a: calls.append("companion_stop")
        m.streams_stop = lambda a: None
        m._streams_static_dir = lambda: "/nonexistent-streams-dir"
        m._relay_is_alive = lambda: True
        m.event_stop([])                       # must not raise
        assert "relay_stop" in calls
    finally:
        (m._build_report_file, m.relay_stop, m.companion_stop,
         m.streams_stop, m._streams_static_dir, m._relay_is_alive) = saved


def t_discord_autoleave_gate():
    assert m._discord_autoleave_enabled({}) is True                         # default on
    assert m._discord_autoleave_enabled({"RACECAST_DISCORD_AUTOLEAVE": "0"}) is False
    assert m._discord_autoleave_enabled({"RACECAST_DISCORD_AUTOLEAVE": "1"}) is True


def t_discord_autoleave_noop_without_creds():
    # flag on (default) but no CLIENT_ID/SECRET -> never builds a client, never raises.
    saved_env = {k: os.environ.get(k) for k in
                 ("RACECAST_DISCORD_AUTOLEAVE", "RACECAST_DISCORD_CLIENT_ID",
                  "RACECAST_DISCORD_CLIENT_SECRET")}
    saved_client = m._discord_voice_client
    try:
        os.environ.pop("RACECAST_DISCORD_AUTOLEAVE", None)                  # default on
        os.environ.pop("RACECAST_DISCORD_CLIENT_ID", None)
        os.environ.pop("RACECAST_DISCORD_CLIENT_SECRET", None)

        def _boom():
            raise AssertionError("must not build a voice client without creds")
        m._discord_voice_client = _boom
        m._discord_autoleave()                                     # must be a silent no-op
    finally:
        m._discord_voice_client = saved_client
        for k, v in saved_env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def t_event_stop_calls_discord_autoleave():
    calls = []
    saved = (m._build_report_file, m._send_report_core, m.relay_stop,
             m.companion_stop, m.streams_stop, m._streams_static_dir,
             m._discord_autoleave, m._relay_is_alive)
    try:
        m._build_report_file = lambda: {"path": "/tmp/r.html", "summary": "s",
                                        "report": {}}
        m._send_report_core = lambda p, report=None, window=None: None
        m.relay_stop = lambda a: None
        m.companion_stop = lambda a: None
        m.streams_stop = lambda a: None
        m._streams_static_dir = lambda: "/nonexistent-streams-dir"
        m._discord_autoleave = lambda: calls.append("autoleave")
        m._relay_is_alive = lambda: True
        m.event_stop([])
        assert "autoleave" in calls
    finally:
        (m._build_report_file, m._send_report_core, m.relay_stop,
         m.companion_stop, m.streams_stop, m._streams_static_dir,
         m._discord_autoleave, m._relay_is_alive) = saved


def t_event_stop_noop_when_already_stopped():
    # The last-part auto-stop (STOP PART Q) already fired `event stop` with its
    # report and teardown. A SECOND stop, such as Control Center "Stop Event"
    # afterwards, runs with the relay already gone and MUST NOT regenerate and
    # re-send a report: relay down means no commentator names and no qualifying
    # marker, a worse report overwriting the good one. With no live relay, event
    # stop is a no-op: no report, no send, no teardown, no autoleave. (#524)
    calls = []
    saved = (m._relay_is_alive, m._build_report_file, m._send_report_core,
             m.relay_stop, m.companion_stop, m.streams_stop, m._discord_autoleave)
    try:
        m._relay_is_alive = lambda: False            # event already stopped
        m._build_report_file = lambda: calls.append("build") or {"path": "", "summary": ""}
        m._send_report_core = lambda p, report=None, window=None: calls.append("send")
        m.relay_stop = lambda a: calls.append("relay_stop")
        m.companion_stop = lambda a: calls.append("companion_stop")
        m.streams_stop = lambda a: calls.append("streams_stop")
        m._discord_autoleave = lambda: calls.append("autoleave")
        m.event_stop([])
        assert calls == [], "already-stopped event stop must be a no-op, got: {}".format(calls)
    finally:
        (m._relay_is_alive, m._build_report_file, m._send_report_core,
         m.relay_stop, m.companion_stop, m.streams_stop, m._discord_autoleave) = saved


def t_qualifying_title_marks_when_qualifying():
    orig = m._relay_mode
    try:
        m._relay_mode = lambda: "qualifying"
        assert m._qualifying_title("Le Mans 24h") == "Le Mans 24h — Qualifying"
        assert m._qualifying_title("") == "Qualifying"
        m._relay_mode = lambda: "race"
        assert m._qualifying_title("Le Mans 24h") == "Le Mans 24h"
        m._relay_mode = lambda: None
        assert m._qualifying_title("Le Mans 24h") == "Le Mans 24h"
    finally:
        m._relay_mode = orig


def t_report_log_is_fresh_window_gate():
    # The freshness gate keeps a file only if its mtime is within the report
    # session (>= start - grace). since=None keeps everything. (#519)
    g = m.REPORT_LOG_FRESHNESS_GRACE_S
    assert m._report_log_is_fresh(0.0, None) is True            # no window -> keep all
    assert m._report_log_is_fresh(1000.0, 1000.0) is True       # exactly at start
    assert m._report_log_is_fresh(1000.0 - g - 1, 1000.0) is False   # older than grace -> stale
    assert m._report_log_is_fresh(1000.0 - g + 1, 1000.0) is True    # within grace
    assert m._report_log_is_fresh(5000.0, 1000.0) is True       # written during the session


def t_report_log_files_drops_stale_source():
    # A leftover current file from a source that did NOT run this session (e.g. a
    # weeks-old runtime/static/logs/feed_*.log from a one-off `racecast streams`
    # test) must not ride along in an unrelated event's report bundle (#519).
    with tempfile.TemporaryDirectory() as d:
        fresh = os.path.join(d, "relay.log")
        stale = os.path.join(d, "feed_53001.log")
        for p in (fresh, stale):
            with open(p, "w") as fh:
                fh.write("x")
        now = time.time()
        os.utime(fresh, (now, now))
        os.utime(stale, (now - 40 * 86400, now - 40 * 86400))   # 40 days old
        saved = m._log_sources
        try:
            m._log_sources = lambda: {"relay": {"files": lambda: [fresh]},
                                      "streams": {"files": lambda: [stale]}}
            # windowed: the 40-day-old streams leftover is excluded
            pairs = m._report_log_files(since=now - 3600)
            assert (("relay", fresh) in pairs) and (("streams", stale) not in pairs), pairs
            # no window -> back-compat, both included
            all_pairs = m._report_log_files()
            assert (("relay", fresh) in all_pairs) and (("streams", stale) in all_pairs)
        finally:
            m._log_sources = saved
def t_profile_env_vars_includes_kind():
    class _RC:  # minimal ResolvedConfig stand-in
        sheet_id = "abc"; sheet_push_url = ""; intro_url = ""; outro_url = ""
        trailer_url = ""
        discord_webhook_url = ""; obs_collection = ""; console_secret = ""
        discord_client_id = ""; discord_client_secret = ""; discord_voice_url = ""
        event_title = ""; name = "Solo League"; logo_path = ""; kind = "solo"
        template = ""
    env = m._profile_env_vars(_RC())
    assert env["RACECAST_KIND"] == "solo"
    assert env["RACECAST_SHEET_ID"] == "abc"


def t_profile_env_vars_includes_template():
    # solo + a chosen starter template -> RACECAST_TEMPLATE flows to the child env
    # (setup-assets.py reads it to pick GT_Racing_Solo_Commentary/GT_Racing_Solo_POV, #303).
    rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc",
                               kind="solo", template="pov")
    assert m._profile_env_vars(rc)["RACECAST_TEMPLATE"] == "pov"
    # endurance profiles leave template empty -> filtered out (byte-identical env)
    rc2 = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc")
    assert "RACECAST_TEMPLATE" not in m._profile_env_vars(rc2)


def t_env_upsert_preserves_other_keys():
    # env_upsert_data must overlay ONLY the given keys. env_write_data (the
    # underlying writer) treats its entries as the complete set and drops any
    # unlisted real key, which would silently delete e.g. RACECAST_OBS_WS_PASSWORD
    # if a naive two-key write were used to persist device selection (#304).
    import tempfile, os as _os
    d = tempfile.mkdtemp(prefix="racecast-envupsert-")
    p = _os.path.join(d, ".env")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# machine knobs\nRACECAST_OBS_WS_PASSWORD=secret\nRACECAST_UI_PORT=8089\n")
    res = m.env_upsert_data({"RACECAST_WEBCAM": "cam0", "RACECAST_CAPTURE": "cap1"}, path=p)
    assert res["ok"], res
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    assert "RACECAST_OBS_WS_PASSWORD=secret" in text   # unrelated key preserved
    assert "RACECAST_UI_PORT=8089" in text
    assert "RACECAST_WEBCAM=cam0" in text
    assert "RACECAST_CAPTURE=cap1" in text
    assert "# machine knobs" in text                   # comment preserved


def t_env_upsert_updates_existing_key_in_place():
    import tempfile, os as _os
    d = tempfile.mkdtemp(prefix="racecast-envupsert2-")
    p = _os.path.join(d, ".env")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("RACECAST_WEBCAM=old\nRACECAST_UI_PORT=8089\n")
    m.env_upsert_data({"RACECAST_WEBCAM": "new"}, path=p)
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    assert "RACECAST_WEBCAM=new" in text and "RACECAST_WEBCAM=old" not in text
    assert "RACECAST_UI_PORT=8089" in text


def t_env_upsert_rejects_foreign_key_clearly():
    # A machine .env that already holds a non-RACECAST_ key (should never happen,
    # but the editor/device-scan must not blow up with the raw env_write_data
    # wording) gets a clear, device-context error and writes nothing. (#304)
    import tempfile, os as _os
    d = tempfile.mkdtemp(prefix="racecast-envupsert-foreign-")
    p = _os.path.join(d, ".env")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("FOO=bar\nRACECAST_UI_PORT=8089\n")
    with open(p, encoding="utf-8") as fh:
        before = fh.read()
    res = m.env_upsert_data({"RACECAST_WEBCAM": "x"}, path=p)
    assert not res["ok"]
    assert "could not be saved" in res["error"]
    with open(p, encoding="utf-8") as fh:
        after = fh.read()
    assert after == before                            # nothing written


def t_resolve_device_selection_by_index_and_id():
    devs = [{"name": "FaceTime", "value": "0x14000000"},
            {"name": "Elgato HD60", "value": "0x14200000"}]
    assert m.resolve_device_selection(devs, "1") == ("0x14000000", None)   # 1-based index
    assert m.resolve_device_selection(devs, "2") == ("0x14200000", None)
    assert m.resolve_device_selection(devs, "Elgato") == ("0x14200000", None)  # name substring
    assert m.resolve_device_selection(devs, "0x14000000") == ("0x14000000", None)  # exact value
    val, err = m.resolve_device_selection(devs, "9")
    assert val is None and "out of range" in err
    val, err = m.resolve_device_selection(devs, "nosuch")
    assert val is None and err
    assert m.resolve_device_selection(devs, "") == (None, None)   # blank = skip/leave


def t_parse_device_scan_args_mic():
    # --mic is a third recognized flag alongside --webcam/--capture, mapping to
    # writing RACECAST_MIC via env_upsert_data, which preserves other keys. (#307)
    assert m._parse_device_scan_args([]) == (None, None, None, None)
    assert m._parse_device_scan_args(["--mic", "2"]) == (None, None, "2", None)
    assert m._parse_device_scan_args(
        ["--webcam", "1", "--capture", "2", "--mic", "3"]) == ("1", "2", "3", None)
    try:
        m._parse_device_scan_args(["--bogus", "x"])
        raise AssertionError("expected ValueError for an unrecognized flag")
    except ValueError:
        pass  # expected: unrecognized flag


def t_parse_device_scan_args_tyres():
    # --tyres is a fourth recognized flag (VIDEO device, resolves against
    # the same enumerated video list as --webcam/--capture), mapping to writing
    # RACECAST_TYRES_CAPTURE.
    assert m._parse_device_scan_args(["--tyres", "2"]) == (None, None, None, "2")
    assert m._parse_device_scan_args(
        ["--webcam", "1", "--tyres", "3"]) == ("1", None, None, "3")


def t_devices_write_data_accepts_mic():
    # devices_write_data (the /api/devices/select backing) accepts a mic value and
    # upserts RACECAST_MIC without disturbing unrelated .env keys (#307).
    import tempfile, os as _os
    d = tempfile.mkdtemp(prefix="racecast-devwrite-mic-")
    p = _os.path.join(d, ".env")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("RACECAST_OBS_WS_PASSWORD=secret\n")
    res = m.devices_write_data(None, None, "MIC-ID", path=p)
    assert res["ok"], res
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    assert "RACECAST_MIC=MIC-ID" in text


def t_devices_write_data_accepts_tyres():
    # devices_write_data accepts a tyres/fuel capture value and upserts
    # RACECAST_TYRES_CAPTURE (Control Center parity with device-scan --tyres).
    import tempfile, os as _os
    d = tempfile.mkdtemp(prefix="racecast-devwrite-tyres-")
    p = _os.path.join(d, ".env")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("RACECAST_OBS_WS_PASSWORD=secret\n")
    res = m.devices_write_data(None, None, None, "TYRE-ID", path=p)
    assert res["ok"], res
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    assert "RACECAST_TYRES_CAPTURE=TYRE-ID" in text
    assert "RACECAST_OBS_WS_PASSWORD=secret" in text        # unrelated key preserved
    assert "RACECAST_OBS_WS_PASSWORD=secret" in text


def t_devices_enumerate_data_maps_probe_result():
    import obs_ws
    saved = obs_ws.probe_device_options
    obs_ws.probe_device_options = lambda *a, **k: {
        "devices": [{"name": "Cam", "value": "0x1", "enabled": True}],
        "note": "",
        "mic": [{"name": "Mic", "value": "uid", "enabled": True}],
        "mic_note": "audio note"}
    try:
        out = m.devices_enumerate_data()
    finally:
        obs_ws.probe_device_options = saved
    assert out["ok"] is True                       # ok reflects the video note only
    assert out["devices"] == [{"name": "Cam", "value": "0x1"}]
    assert out["note"] == ""
    assert out["mic"] == [{"name": "Mic", "value": "uid"}]
    assert out["mic_note"] == "audio note"


def t_devices_enumerate_data_note_sets_ok_false():
    import obs_ws
    saved = obs_ws.probe_device_options
    obs_ws.probe_device_options = lambda *a, **k: {
        "devices": [], "note": "OBS unreachable", "mic": [], "mic_note": "OBS unreachable"}
    try:
        out = m.devices_enumerate_data()
    finally:
        obs_ws.probe_device_options = saved
    assert out["ok"] is False
    assert out["note"] == "OBS unreachable"


def t_route_device_scan():
    # device-scan is a single-word command (like freeport/links) that forwards
    # any flags (--webcam/--capture) straight through to device_scan_cmd (#304).
    assert m.route(["device-scan"]) == {"kind": "device-scan", "rest": []}
    assert m.route(["device-scan", "--webcam", "1", "--capture", "2"]) == \
        {"kind": "device-scan", "rest": ["--webcam", "1", "--capture", "2"]}


def t_resolve_console_prefers_relay():
    # A running relay that already latched a console short-circuits the scan.
    out = m.resolve_console(
        relay_get=lambda: {"source": "192.168.1.42"},
        discover=lambda **k: (_ for _ in ()).throw(AssertionError("must not scan")))
    assert out == {"consoles": ["192.168.1.42"], "note": "", "from_relay": True}


def t_resolve_console_scans_when_no_relay():
    # No relay (relay_get returns None) -> fall through to the scanner.
    out = m.resolve_console(
        relay_get=lambda: None,
        discover=lambda **k: {"consoles": ["192.168.1.50"], "note": ""})
    assert out == {"consoles": ["192.168.1.50"], "note": "", "from_relay": False}


def t_resolve_console_scans_when_relay_unlatched():
    # Relay up but telemetry not yet latched (source None) -> scan.
    out = m.resolve_console(
        relay_get=lambda: {"source": None},
        discover=lambda **k: {"consoles": [], "note": "nope"})
    assert out == {"consoles": [], "note": "nope", "from_relay": False}


def t_route_gt7_discover():
    assert m.route(["gt7-discover"]) == {"kind": "gt7-discover", "rest": []}
    assert m.route(["gt7-discover", "--save", "--timeout", "6"]) == \
        {"kind": "gt7-discover", "rest": ["--save", "--timeout", "6"]}


def t_parse_gt7_discover_args():
    assert m._parse_gt7_discover_args([]) == (False, False, 4.0, None)
    assert m._parse_gt7_discover_args(["--save"]) == (True, False, 4.0, None)
    assert m._parse_gt7_discover_args(["--print"]) == (False, True, 4.0, None)
    assert m._parse_gt7_discover_args(["--timeout", "6"]) == (False, False, 6.0, None)
    assert m._parse_gt7_discover_args(["--pick", "2"]) == (False, False, 4.0, "2")
    try:
        m._parse_gt7_discover_args(["--nope"])
    except ValueError as e:
        assert "usage:" in str(e)
    else:
        raise AssertionError("expected ValueError")


def t_ps_ip_write_validates_and_upserts(tmp_path=None):
    import tempfile, os as _os
    fd, path = tempfile.mkstemp(suffix=".env"); _os.close(fd)
    try:
        bad = m.ps_ip_write_data("not a host!", path=path)
        assert bad["ok"] is False and "invalid" in bad["error"].lower()
        good = m.ps_ip_write_data("192.168.1.42", path=path)
        assert good["ok"] is True
        with open(path, encoding="utf-8") as f:
            assert "RACECAST_GT7_PS_IP=192.168.1.42" in f.read()
    finally:
        _os.remove(path)


def t_gt7_discover_cmd_single_save(capsys=None):
    # One console found + --save -> writes RACECAST_GT7_PS_IP via ps_ip_write_data
    # (both resolve_console and ps_ip_write_data are mocked, so no real .env is touched).
    saved_resolve = m.resolve_console
    saved_write = m.ps_ip_write_data
    writes = {}
    m.resolve_console = lambda **k: {"consoles": ["192.168.1.42"], "note": "",
                                     "from_relay": True}
    m.ps_ip_write_data = lambda ip, path=None: writes.update(ip=ip) or {"ok": True}
    try:
        m.gt7_discover_cmd(["--save"])
    finally:
        m.resolve_console = saved_resolve
        m.ps_ip_write_data = saved_write
    assert writes["ip"] == "192.168.1.42"


# The GUI launch environment.


class _FakeApp:
    """Minimal `event` stand-in: one GUI app, optional session overrides."""

    def __init__(self, overrides=None):
        self._overrides = {} if overrides is None else overrides

    def launch_command(self, app, platform, **kw):
        return (["/usr/bin/obs"], None)

    def launch_env(self, app, platform):
        return dict(self._overrides)


class _FakeProc:
    """Popen stand-in. rc=None = still running; a number = already exited."""

    def __init__(self, rc=None):
        self._rc = rc

    def poll(self):
        return self._rc


def _launch(ev, popen, app="obs"):
    """Drive _event_launch through its seams, mutating no stdlib module. The house
    style injects the same way (preflight.tool_version(run=…))."""
    import install_apps
    saved = install_apps.app_present
    try:
        install_apps.app_present = lambda a, platform: True
        return m._event_launch(ev, app, popen=popen, sleep=lambda _s: None)
    finally:
        install_apps.app_present = saved


def t_event_launch_strips_the_pyinstaller_library_path():
    """OBS and Discord link the SYSTEM libraries. Handing them the frozen binary's
    LD_LIBRARY_PATH makes them load our bundled libssl and die with "version
    `OPENSSL_3.2.0' not found" before they can even write a log, and stderr=DEVNULL
    hides it so `event start` only says "still not up". (#572)
    """
    seen = {}
    saved_env = m.sv.external_tool_env
    m.sv.external_tool_env = lambda *a, **k: {"PATH": "/usr/bin", "DISPLAY": ":0"}
    try:
        ok = _launch(_FakeApp({"XDG_RUNTIME_DIR": "/run/user/1000"}),
                     lambda argv, **kw: seen.update(kw) or _FakeProc())
    finally:
        m.sv.external_tool_env = saved_env
    assert ok is True
    env = seen.get("env") or {}
    # Never assert with `env` as the message: it would dump the whole
    # process environment (tokens included) into the test output on failure.
    assert "LD_LIBRARY_PATH" not in env, "LD_LIBRARY_PATH reached the GUI app"
    # The session overrides still have to reach the app (PR #560).
    assert env.get("XDG_RUNTIME_DIR") == "/run/user/1000", "session override lost"
    assert env.get("DISPLAY") == ":0", "external_tool_env base not used"


def t_event_launch_strips_it_even_without_session_overrides():
    """macOS/Windows have no session overrides, but a frozen parent still puts
    its bundle on DYLD_/LD_LIBRARY_PATH. Passing env=None there would inherit it."""
    seen = {}
    saved_env = m.sv.external_tool_env
    m.sv.external_tool_env = lambda *a, **k: {"PATH": "/usr/bin"}
    try:
        ok = _launch(_FakeApp(), lambda argv, **kw: seen.update(kw) or _FakeProc())
    finally:
        m.sv.external_tool_env = saved_env
    assert ok is True
    assert seen.get("env") == {"PATH": "/usr/bin"}, "frozen env not passed through"


def t_event_launch_reports_an_app_that_dies_immediately():
    """The reason #572 stayed invisible: OBS said exactly what was wrong and the
    message went to DEVNULL. An app already gone after the grace window now has
    its own words printed."""
    saved_env = m.sv.external_tool_env
    m.sv.external_tool_env = lambda *a, **k: {"PATH": "/usr/bin"}

    def _dies(argv, **kw):
        kw["stderr"].write(b"obs: libssl.so.3: version `OPENSSL_3.2.0' not found\n")
        return _FakeProc(rc=127)

    out = io.StringIO()
    saved_stdout = sys.stdout
    sys.stdout = out
    try:
        _launch(_FakeApp(), _dies)
    finally:
        sys.stdout = saved_stdout
        m.sv.external_tool_env = saved_env
    printed = out.getvalue()
    assert "exited immediately" in printed, printed
    assert "OPENSSL_3.2.0" in printed, printed


def t_gui_spawn_keeps_quiet_about_a_healthy_app():
    """A live GUI app's stderr is not ours to keep: no note, nothing printed."""
    out = io.StringIO()
    saved_stdout = sys.stdout
    sys.stdout = out
    try:
        note = m._gui_spawn(["/usr/bin/obs"], None, None, "obs",
                            popen=lambda argv, **kw: _FakeProc(), sleep=lambda _s: None)
    finally:
        sys.stdout = saved_stdout
    assert note == ""
    assert out.getvalue() == "", out.getvalue()
# Smoke-test glue. http_util.post_json returns the RAW BODY (unlike get_json,
# which parses), so every POST site has to decode it. The pure smoketest module
# cannot catch that, so these exercise the glue in racecast.py itself with the
# network stubbed.

class _StubHttp:
    """Records calls and replays canned bodies, in http_util's own return shapes."""

    # The real module re-exports this so callers never import urllib to catch it.
    import urllib.error as _ue
    HTTPError = _ue.HTTPError
    del _ue

    def __init__(self, post=b"{}", get=b"{}"):
        self._post, self._get = post, get
        self.posts = []

    def post_json(self, url, obj, *, headers=None, timeout=None):
        self.posts.append((url, obj))
        return self._post

    def get_bytes(self, url, *, headers=None, timeout=None):
        return self._get

    def get_json(self, url, *, headers=None, timeout=None):
        import json as _json
        return _json.loads(self._get.decode("utf-8"))


def _with_stub_http(stub, fn):
    saved = m.http_util
    m.http_util = stub
    try:
        return fn()
    finally:
        m.http_util = saved


def t_smoke_push_reads_the_webhook_ok_flag():
    stub = _StubHttp(post=b'{"ok": true}')
    ok, note = _with_stub_http(stub, lambda: m._smoke_push("https://example.invalid/w", 1, "u"))
    assert ok and note == "", note
    assert stub.posts[0][1] == {"action": "schedule", "row": 1, "url": "u"}, \
        "column A only: Streamer and Stint must never be sent"


def t_smoke_push_reports_a_rejected_write():
    stub = _StubHttp(post=b'{"ok": false, "error": "nope"}')
    ok, _note = _with_stub_http(stub, lambda: m._smoke_push("https://example.invalid/w", 1, "u"))
    assert not ok


def _write_schedule(pushes, sheets, clear=(2,), rows=None):
    """Drive _smoke_write_schedule with a scripted sheet: `sheets` is the list of
    CSV row-lists returned on successive polls."""
    rows = [(2, "https://www.youtube.com/watch?v=1")] if rows is None else rows
    said, seen = [], iter(sheets)
    saved = {n: getattr(m, n) for n in ("_smoke_push", "_smoke_schedule_rows",
                                        "SMOKE_READBACK_S")}
    last = [sheets[-1]]
    m._smoke_push = pushes
    m._smoke_schedule_rows = lambda sheet_id: next(seen, last[0])
    # 0 s: each phase gets exactly one poll, because the deadline is checked AFTER
    # the match attempt, so the loop never reaches its sleep. Keeps the suite fast.
    m.SMOKE_READBACK_S = 0
    try:
        return m._smoke_write_schedule("https://example.invalid/w", "sheet",
                                       rows, list(clear), said.append), said
    finally:
        for n, fn in saved.items():
            setattr(m, n, fn)


def t_smoke_write_schedule_confirms_the_clear_then_the_write():
    """Two confirmations, not one: the cleared rows must come back EMPTY before
    the write, then every row must carry ITS url."""
    sheets = [[["URL", "Streamer"], ["", "A"]],                       # cleared
              [["URL", "Streamer"], ["https://www.youtube.com/watch?v=1", "A"]]]
    (ok, note, notes), said = _write_schedule(lambda u, r, v: (True, ""), sheets)
    assert ok, note
    assert notes == []
    assert any("cleared rows confirmed empty" in line for line in said), said


def t_smoke_write_schedule_survives_a_push_that_reported_an_error():
    """The webhook's HTTP result is not proof; the sheet is. A reported failure
    must not abort when the sheet ends up right, and it is NOT retried."""
    sheets = [[["URL", "Streamer"], ["", "A"]],
              [["URL", "Streamer"], ["https://www.youtube.com/watch?v=1", "A"]]]
    (ok, _note, notes), said = _write_schedule(
        lambda u, r, v: (False, "HTTP 404"), sheets)
    assert ok
    assert notes and any("write row 2" in n for n in notes), notes
    assert any("a push had reported an error" in line for line in said), said


def t_smoke_write_schedule_fails_when_nothing_changed():
    """The hole a plain `want <= got` left open: a repeat run against a DEAD
    webhook wants the URLs the previous run already wrote, so an end-state check
    passes while nothing was written. Confirming the CLEAR first closes it."""
    unchanged = [["URL", "Streamer"], ["https://www.youtube.com/watch?v=1", "A"]]
    ok, note, _notes = _write_schedule(
        lambda u, r, v: (False, "HTTP 404"), [unchanged])[0]
    assert not ok
    assert "cleared" in note and "HTTP 404" in note, note


def t_smoke_write_schedule_fails_when_a_url_lands_in_the_wrong_row():
    """Per row, not set inclusion: the right URL in the wrong row is not a pass."""
    sheets = [[["URL", "S"], ["", "A"], ["", "B"]],
              [["URL", "S"], ["", "A"], ["https://www.youtube.com/watch?v=1", "B"]]]
    ok, note, _n = _write_schedule(lambda u, r, v: (True, ""), sheets,
                                   clear=(2, 3),
                                   rows=[(2, "https://www.youtube.com/watch?v=1")])[0]
    assert not ok and "written" in note, note


def t_smoke_push_never_reports_the_webhook_url():
    """http.client.InvalidURL carries the whole Apps Script path, which is the
    sheet's write capability, and does NOT inherit from ValueError, so it passes
    the JSON guard into stdout, --json and the history file."""
    import http.client

    class _Bad(_StubHttp):
        def post_json(self, url, obj, *, headers=None, timeout=None):
            raise http.client.InvalidURL(
                "URL can't contain control characters. "
                "'/macros/s/AKfycbSECRETID12345/ex ec' (found at least ' ')")

    ok, note = _with_stub_http(_Bad(),
                               lambda: m._smoke_push("https://example.invalid/w", 2, "u"))
    assert not ok
    assert "AKfycb" not in note and "macros" not in note, note
    assert note == "InvalidURL", note


def t_smoke_relay_post_returns_a_parsed_reply():
    stub = _StubHttp(post=b'{"scene": "Standby", "muted": {}}')
    got = _with_stub_http(stub, lambda: m._smoke_relay_post("obs/state", {}))
    assert got["scene"] == "Standby"      # a bytes reply would have no .get


def t_smoke_twitch_candidates_parses_the_gql_reply():
    body = (b'{"data": {"game": {"streams": {"edges": [{"node": '
            b'{"title": "t", "viewersCount": 9, "broadcaster": {"login": "someone"}}}]}}}}')
    stub = _StubHttp(post=body)
    got = _with_stub_http(stub, lambda: m._smoke_twitch_candidates("iRacing"))
    assert [c["login"] for c in got] == ["someone"]


def t_smoke_twitch_candidates_survives_a_junk_reply():
    """Discovery is best effort: a malformed body must not abort the run."""
    stub = _StubHttp(post=b"<html>503</html>")
    got = _with_stub_http(stub, lambda: m._smoke_twitch_candidates("iRacing"))
    assert got == []


def _http_error(code, body):
    import io, urllib.error
    return urllib.error.HTTPError("http://127.0.0.1/x", code, "err", {},
                                  io.BytesIO(body))


def t_smoke_apply_split_is_one_relay_call():
    """#591: the rundown's SPLIT names no source. It cuts, then leaves the
    visibility and the audio to the relay, exactly like the panel and board."""
    stub = _StubHttp(post=b'{"ok": true}')
    split = next(s for s in m._sm().RUNDOWN if s.label == "SPLIT")
    _with_stub_http(stub, lambda: m._smoke_apply(split))
    paths = [url.rsplit(":8088/", 1)[-1] for url, _ in stub.posts]
    assert paths == ["obs/scene", "obs/split"], paths
    assert stub.posts[0][1] == {"scene": "Splitscreen"}, stub.posts


def t_smoke_apply_stint_is_one_relay_call():
    """STINT A/B cut, then take visibility, audio and the commentary mic from the
    relay (/obs/stint), the same call the Director Panel and Companion make."""
    stub = _StubHttp(post=b'{"ok": true}')
    for label, feed in (("STINT A", "A"), ("STINT B", "B")):
        stub.posts.clear()
        step = next(s for s in m._sm().RUNDOWN if s.label == label)
        _with_stub_http(stub, lambda step=step: m._smoke_apply(step))
        paths = [url.rsplit(":8088/", 1)[-1] for url, _ in stub.posts]
        assert paths == ["obs/scene", "obs/stint"], (label, paths)
        assert stub.posts[1][1] == {"feed": feed}, stub.posts


def t_smoke_relay_post_keeps_the_error_body_of_a_503():
    """The relay answers a dead OBS with 503 + {"error": "obs unavailable"}.
    urllib raises on 5xx, so without reading the body back the run only ever sees
    "HTTP Error 503" and the SPLIT step fails hard instead of warning."""
    class _Raiser(_StubHttp):
        def post_json(self, url, obj, *, headers=None, timeout=None):
            raise _http_error(503, b'{"error": "obs unavailable"}')

    got = _with_stub_http(_Raiser(), lambda: m._smoke_relay_post("obs/split", {}))
    assert got["error"] == "obs unavailable", got
    assert m._sm().step_error_verdict(got["error"])[0] == m._sm().WARN


def t_smoke_relay_post_falls_back_to_the_status_code():
    """A non-JSON error page must still produce a readable note, not a traceback."""
    class _Raiser(_StubHttp):
        def post_json(self, url, obj, *, headers=None, timeout=None):
            raise _http_error(500, b"<html>boom</html>")

    got = _with_stub_http(_Raiser(), lambda: m._smoke_relay_post("obs/state", {}))
    assert "500" in got["error"], got


def t_smoke_capture_de_pyinstallers_the_child_environment():
    """yt-dlp and streamlink run under the SYSTEM python, and the frozen bootloader
    puts our bundled libcrypto on their library path, so they die and the
    fingerprint reports them as missing tools. services.external_tool_env() is the
    house fix and is None off the frozen binary, so a source run is unaffected."""
    seen = {}
    saved_run, saved_env = m.subprocess.run, m.sv.external_tool_env
    sentinel = {"LD_LIBRARY_PATH": "/usr/lib"}

    class _P:
        returncode, stdout, stderr = 0, "ok", ""

    m.sv.external_tool_env = lambda *a, **k: sentinel
    m.subprocess.run = lambda argv, **kw: seen.update(kw) or _P()
    try:
        rc, txt = m._smoke_capture(["yt-dlp", "--version"])
    finally:
        m.subprocess.run, m.sv.external_tool_env = saved_run, saved_env
    assert rc == 0 and txt == "ok"
    assert seen.get("env") is sentinel, seen.get("env")


def t_smoke_capture_separates_a_missing_tool_from_a_broken_one():
    """'not on PATH' must not be reported for a tool that is present but broken."""
    saved = m.subprocess.run

    def _boom(argv, **kw):
        raise FileNotFoundError(argv[0])

    m.subprocess.run = _boom
    try:
        rc, txt = m._smoke_capture(["nope", "--version"])
    finally:
        m.subprocess.run = saved
    assert rc == 127 and "not found" in txt



def t_rundown_reports_each_step_once():
    """An ARM step must appear once. If the byte-wait branch falls through into the
    scene-less branch it appends a second result under the same name, inflating
    the count and half-reporting a failing arm."""
    saved = {name: getattr(m, name) for name in
             ("_smoke_relay_get", "_smoke_relay_post", "_smoke_apply",
              "_smoke_wait_bytes")}
    m._smoke_relay_get = lambda path, **kw: {"manual_feed_arm": True,
                                             "live": {"feed": "B"}}
    m._smoke_relay_post = lambda path, body, **kw: {"ok": True, "scene": None,
                                                    "sources": [], "audio": []}
    m._smoke_apply = lambda step: {"ok": True}
    m._smoke_wait_bytes = lambda which, say: True
    saved_pause = m.SMOKE_STEP_PAUSE_S
    m.SMOKE_STEP_PAUSE_S = 0
    try:
        results = m._smoke_rundown(lambda msg: None)
    finally:
        for name, fn in saved.items():
            setattr(m, name, fn)
        m.SMOKE_STEP_PAUSE_S = saved_pause
    names = [r.name for r in results]
    assert len(names) == len(set(names)), [n for n in names if names.count(n) > 1]
    assert len(names) == len(m._sm().RUNDOWN), (len(names), len(m._sm().RUNDOWN))

def t_smoketest_tears_down_even_when_event_start_aborts():
    """`event start` exits non-zero when its readiness report has a FAIL, but by
    then the relay, Companion and a PUBLIC Funnel are already up. Arming the
    teardown only on a successful bring-up leaves all three running, with no
    verdict printed at all."""
    calls = []
    saved = {name: getattr(m, name) for name in
             ("_active_profile_name", "_relay_is_alive", "_smoke_confirm",
              "_smoke_fingerprint", "_smoke_vocab", "_smoke_discover",
              "_smoke_js_runtime", "_smoke_schedule_rows", "_smoke_write_schedule",
              "_smoke_append_history", "event_start", "event_stop")}
    saved_env = {k: os.environ.get(k) for k in
                 ("RACECAST_SHEET_ID", "RACECAST_SHEET_PUSH_URL")}
    os.environ["RACECAST_SHEET_ID"] = "sheet"
    os.environ["RACECAST_SHEET_PUSH_URL"] = "https://example.invalid/w"
    m._active_profile_name = lambda *a, **k: "testing"
    m._relay_is_alive = lambda *a, **k: False
    m._smoke_confirm = lambda *a, **k: None
    m._smoke_fingerprint = lambda: {"yt-dlp": "1", "js_runtime": "unknown"}
    m._smoke_vocab = lambda sheet_id: (["q"], ["c"])
    m._smoke_discover = lambda *a, **k: (["https://www.youtube.com/watch?v=1",
                                          "https://www.youtube.com/watch?v=2"],
                                         ["https://www.twitch.tv/x"], [])
    m._smoke_js_runtime = lambda url: "deno-x"
    m._smoke_schedule_rows = lambda sheet_id: [["URL", "Streamer"], ["a", "b"],
                                               ["c", "d"], ["e", "f"]]
    m._smoke_write_schedule = lambda *a, **k: (True, "", [])
    m._smoke_append_history = lambda entry: calls.append("history")

    def _boom(rest):
        calls.append("start")
        raise SystemExit(1)          # event_status exits 1 when FAILs remain

    m.event_start = _boom
    m.event_stop = lambda rest: calls.append("stop")
    try:
        try:
            m.smoketest_cmd(["--json", "--minutes", "1"])
        except SystemExit as exc:
            calls.append(f"exit{exc.code}")
    finally:
        for name, fn in saved.items():
            setattr(m, name, fn)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert "start" in calls, calls
    assert "stop" in calls, "teardown skipped: services left running"
    assert "history" in calls, "verdict/history lost"
    assert "exit1" in calls, calls



def t_smoketest_runs_the_rundown_when_event_start_exits_zero():
    """`event start` ends through event_status, which exits 0 when the stack is
    ready. Treating every SystemExit as an abort skips the whole rundown on a
    healthy bring-up."""
    calls = []
    saved = {name: getattr(m, name) for name in
             ("_active_profile_name", "_relay_is_alive", "_smoke_confirm",
              "_smoke_fingerprint", "_smoke_vocab", "_smoke_discover",
              "_smoke_js_runtime", "_smoke_schedule_rows", "_smoke_write_schedule",
              "_smoke_append_history", "event_start", "event_stop",
              "_smoke_rundown", "_smoke_observe")}
    saved_env = {k: os.environ.get(k) for k in
                 ("RACECAST_SHEET_ID", "RACECAST_SHEET_PUSH_URL")}
    os.environ["RACECAST_SHEET_ID"] = "sheet"
    os.environ["RACECAST_SHEET_PUSH_URL"] = "https://example.invalid/w"
    m._active_profile_name = lambda *a, **k: "testing"
    m._relay_is_alive = lambda *a, **k: False
    m._smoke_confirm = lambda *a, **k: None
    m._smoke_fingerprint = lambda: {"yt-dlp": "1", "js_runtime": "unknown"}
    m._smoke_vocab = lambda sheet_id: (["q"], ["c"])
    m._smoke_discover = lambda *a, **k: (["https://www.youtube.com/watch?v=1",
                                          "https://www.youtube.com/watch?v=2"],
                                         ["https://www.twitch.tv/x"], [])
    m._smoke_js_runtime = lambda url: "deno-x"
    m._smoke_schedule_rows = lambda sheet_id: [["URL", "Streamer"],
                                               ["https://www.youtube.com/watch?v=o", "A"],
                                               ["https://www.twitch.tv/o", "B"],
                                               ["https://youtu.be/o", "C"]]
    m._smoke_write_schedule = lambda *a, **k: (True, "", [])
    m._smoke_append_history = lambda entry: None
    m.event_start = lambda rest: (_ for _ in ()).throw(SystemExit(0))
    m.event_stop = lambda rest: calls.append("stop")
    m._smoke_rundown = lambda say: calls.append("rundown") or []
    m._smoke_observe = lambda minutes, say: calls.append("observe") or []
    try:
        try:
            m.smoketest_cmd(["--json", "--minutes", "1"])
        except SystemExit:
            # The command exits through SystemExit by design; this test asserts
            # on the teardown it ran on the way out, not on the exit code.
            pass
    finally:
        for name, fn in saved.items():
            setattr(m, name, fn)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert calls == ["rundown", "observe", "stop"], calls

def t_smoketest_teardown_failure_reaches_the_verdict():
    """A failing `event stop` has to be a check, not a say() note: a note is
    suppressed entirely under --json, so a run prints PASS, exits 0 and leaves the
    relay pulling two strangers' streams."""
    seen = {}
    saved = {name: getattr(m, name) for name in
             ("_active_profile_name", "_relay_is_alive", "_smoke_confirm",
              "_smoke_fingerprint", "_smoke_vocab", "_smoke_discover",
              "_smoke_js_runtime", "_smoke_schedule_rows", "_smoke_write_schedule",
              "_smoke_append_history", "event_start", "event_stop",
              "_smoke_rundown", "_smoke_observe")}
    saved_env = {k: os.environ.get(k) for k in
                 ("RACECAST_SHEET_ID", "RACECAST_SHEET_PUSH_URL")}
    os.environ["RACECAST_SHEET_ID"] = "sheet"
    os.environ["RACECAST_SHEET_PUSH_URL"] = "https://example.invalid/w"
    m._active_profile_name = lambda *a, **k: "testing"
    m._relay_is_alive = lambda *a, **k: False
    m._smoke_confirm = lambda *a, **k: None
    m._smoke_fingerprint = lambda: {"yt-dlp": "1", "js_runtime": "unknown"}
    m._smoke_vocab = lambda sheet_id: (["q"], ["c"])
    m._smoke_discover = lambda *a, **k: (["https://www.youtube.com/watch?v=1",
                                          "https://www.youtube.com/watch?v=2"],
                                         ["https://www.twitch.tv/x"], [])
    m._smoke_js_runtime = lambda url: "deno-x"
    m._smoke_schedule_rows = lambda sheet_id: [["URL", "Streamer"],
                                               ["https://www.youtube.com/watch?v=o", "A"],
                                               ["https://www.twitch.tv/o", "B"],
                                               ["https://youtu.be/o", "C"]]
    m._smoke_write_schedule = lambda *a, **k: (True, "", [])
    m._smoke_append_history = lambda entry: seen.update(entry=entry)
    m.event_start = lambda rest: None
    m._smoke_rundown = lambda say: []
    m._smoke_observe = lambda minutes, say: []

    def _stop_boom(rest):
        raise SystemExit("relay stop failed")

    m.event_stop = _stop_boom
    code = None
    try:
        try:
            m.smoketest_cmd(["--json", "--minutes", "1"])
        except SystemExit as exc:
            code = exc.code
    finally:
        for name, fn in saved.items():
            setattr(m, name, fn)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    names = [c["name"] for c in (seen.get("entry") or {}).get("checks", [])]
    assert "teardown" in names, names
    assert code == 1, code
    # The URLs the run blanked are recorded; nothing restores them.
    assert (seen.get("entry") or {}).get("cleared"), "pre-run URLs not recorded"


# _open_url: the browser must not inherit the frozen library path. Under
# LD_LIBRARY_PATH=/tmp/_MEI… even /bin/bash dies with "undefined symbol:
# rl_print_keybinding", so google-chrome never starts, and webbrowser sends the
# child's stderr to devnull so nothing says why. (#572, #573)

def t_url_opener_argv_prefers_xdg_open():
    got = m.url_opener_argv("linux", "http://127.0.0.1:8089/",
                            which=lambda t: "/usr/bin/" + t)
    assert got == ["/usr/bin/xdg-open", "http://127.0.0.1:8089/"], got


def t_url_opener_argv_gio_ends_option_parsing():
    """gio takes the URL after an `open` verb; `--` stops option parsing there,
    matching what CPython's own webbrowser passes."""
    got = m.url_opener_argv("linux", "http://h/",
                            which=lambda t: "/usr/bin/gio" if t == "gio" else None)
    assert got == ["/usr/bin/gio", "open", "--", "http://h/"], got


def t_url_opener_argv_macos_uses_open():
    got = m.url_opener_argv("darwin", "http://h/", which=lambda t: "/usr/bin/" + t)
    assert got == ["/usr/bin/open", "http://h/"], got


def t_url_opener_argv_refuses_anything_but_http():
    """`open`/`gio open` launch a FILE with its default application. Only ever
    spawn them for a real http(s) URL, so a future caller handing over a path, or
    a `-`-leading string, can never reach them."""
    for plat in ("linux", "darwin"):
        for bad in ("/etc/passwd", "-x", "file:///etc/passwd", "javascript:x", ""):
            assert m.url_opener_argv(plat, bad, which=lambda t: "/usr/bin/" + t) is None, (plat, bad)


def t_url_opener_argv_none_when_there_is_nothing_to_spawn():
    assert m.url_opener_argv("win32", "http://h/", which=lambda t: "C:/x") is None, \
        "windows has no library-path problem, so there is nothing to fix there"
    assert m.url_opener_argv("linux", "http://h/", which=lambda t: None) is None, \
        "a Linux box without any opener: fall back rather than invent one"


class _FakeOpenerProc:
    """Popen stand-in for the URL opener: `rc` None = still running, else exit code."""

    def __init__(self, rc):
        self._rc = rc

    def poll(self):
        return self._rc


def _capture_open_url(env, platform="linux", which=lambda t: "/usr/bin/" + t,
                      rc=0, stderr_text=b"", raises=None):
    """Run _open_url with stubbed seams. Returns (popen_calls, browser_calls, out)."""
    popen_calls, wb_calls, out = [], [], []

    def _popen(argv, **kw):
        if raises is not None:
            raise raises
        popen_calls.append((argv, kw))
        if stderr_text:
            kw["stderr"].write(stderr_text)
        return _FakeOpenerProc(rc)

    import contextlib
    saved_env = m.sv.external_tool_env
    buf = io.StringIO()
    try:
        m.sv.external_tool_env = lambda *a, **k: env
        with contextlib.redirect_stdout(buf):
            m._open_url("http://127.0.0.1:8089/", which=which, platform=platform,
                        popen=_popen, sleep=lambda _s: None, browser=wb_calls.append)
    finally:
        m.sv.external_tool_env = saved_env
    out.extend(buf.getvalue().splitlines())
    return popen_calls, wb_calls, out


def t_open_url_hands_the_opener_a_de_pyinstaller_env():
    clean = {"PATH": "/usr/bin"}          # what external_tool_env returns when frozen
    popen_calls, wb_calls, _out = _capture_open_url(clean)
    assert not wb_calls, "webbrowser.open would inherit the frozen environment"
    assert len(popen_calls) == 1, popen_calls
    argv, kw = popen_calls[0]
    assert argv == ["/usr/bin/xdg-open", "http://127.0.0.1:8089/"], argv
    assert kw["env"] is clean, kw
    # Detached via the repo's own per-OS helper, not a hardcoded flag. Asserting
    # the helper's OWN answer, never `start_new_session is True`: on the Windows
    # runner that key does not exist (creationflags does), so pinning one OS's
    # answer here goes green on macOS/Linux and red on windows-latest, the same
    # cross-platform trap CLAUDE.md records for os.path.join.
    for key, val in m.sv.spawn_kwargs(os.name).items():
        assert kw.get(key) == val, (key, kw)
    # stderr is CAPTURED, never DEVNULL; a silent linker death is invisible. (#572)
    assert kw["stderr"] is not m.subprocess.DEVNULL, kw


def t_url_opener_detaches_per_os():
    """Both branches of the detach helper, checked from any OS. The matrix runs
    this file on Windows too, where the POSIX answer is simply absent."""
    assert m.sv.spawn_kwargs("posix") == {"start_new_session": True}
    assert "creationflags" in m.sv.spawn_kwargs("nt")


def t_open_url_treats_a_clean_exit_as_success():
    """A URL opener HANDS OVER and exits. Unlike a GUI app, being gone is the
    normal case, so the exit CODE decides, not whether it is still running."""
    _popen, wb_calls, _out = _capture_open_url({"PATH": "/usr/bin"}, rc=0)
    assert not wb_calls, "a successful xdg-open must not also open a second browser"


def t_open_url_reports_a_failing_opener_then_falls_back():
    popen_calls, wb_calls, out = _capture_open_url(
        {"PATH": "/usr/bin"}, rc=3, stderr_text=b"gio: no handler\nsecond line\n")
    assert len(popen_calls) == 1
    assert wb_calls == ["http://127.0.0.1:8089/"], wb_calls
    joined = " | ".join(out)
    assert "no handler" in joined, joined            # the opener's own words
    assert "open http://127.0.0.1:8089/ yourself" in joined, joined


def t_open_url_reports_a_bare_exit_code_when_the_opener_said_nothing():
    _p, wb_calls, out = _capture_open_url({"PATH": "/usr/bin"}, rc=4)
    assert wb_calls, "a non-zero exit must still fall back"
    assert "exit 4" in " | ".join(out), out


def t_open_url_falls_back_when_the_opener_cannot_start():
    """Popen itself raises when the opener vanishes between which() and the spawn."""
    _p, wb_calls, out = _capture_open_url({"PATH": "/usr/bin"},
                                          raises=OSError("No such file"))
    assert wb_calls == ["http://127.0.0.1:8089/"], wb_calls
    assert "No such file" in " | ".join(out), out


def t_open_url_uses_webbrowser_off_the_frozen_build():
    """external_tool_env() returns None when not frozen, so a source run must keep
    webbrowser's own browser discovery, unchanged."""
    popen_calls, wb_calls, _out = _capture_open_url(None)
    assert wb_calls == ["http://127.0.0.1:8089/"], wb_calls
    assert not popen_calls, popen_calls


def t_open_url_falls_back_when_there_is_no_opener():
    popen_calls, wb_calls, _out = _capture_open_url({"PATH": "/usr/bin"},
                                                    which=lambda t: None)
    assert wb_calls == ["http://127.0.0.1:8089/"], wb_calls
    assert not popen_calls, popen_calls


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

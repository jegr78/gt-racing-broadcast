#!/usr/bin/env python3
"""Stdlib checks for the spawned-service daemon helper. Run: python3 tests/test_services.py"""
import os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import services as sv


def t_read_pid_valid(tmp):
    p = os.path.join(tmp, "x.pid")
    with open(p, "w") as fh:
        fh.write("4321\n")
    assert sv.read_pid(p) == 4321


def t_read_pid_missing_or_garbage(tmp):
    assert sv.read_pid(os.path.join(tmp, "nope.pid")) is None
    p = os.path.join(tmp, "g.pid")
    with open(p, "w") as fh:
        fh.write("not-a-pid")
    assert sv.read_pid(p) is None


def t_pid_alive_self_and_dead():
    assert sv.pid_alive(os.getpid()) is True
    assert sv.pid_alive(0) is False
    assert sv.pid_alive(2_000_000_000) is False   # implausibly high → not alive


def t_status_line_running_and_stopped():
    assert sv.status_line("relay", 99, True).startswith("relay")
    assert "RUNNING (pid 99)" in sv.status_line("relay", 99, True)
    assert "stopped" in sv.status_line("relay", None, False)


def t_start_detached_then_stop(tmp):
    log = os.path.join(tmp, "logs", "svc.log")
    pidf = os.path.join(tmp, "svc.pid")
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    pid = sv.start_detached(argv, log, pidf)
    assert sv.pid_alive(pid) is True
    assert sv.read_pid(pidf) == pid
    assert sv.stop_pid(pid, pidf, timeout=5) is True
    assert sv.pid_alive(pid) is False
    assert not os.path.exists(pidf)   # pid file removed on stop


def t_looks_like_relay():
    # frozen binary running `relay run`
    assert sv.looks_like_relay("/opt/racecast/racecast relay run --runtime /x")
    # repo mode: python running the relay script
    assert sv.looks_like_relay("python3 /a/src/relay/racecast-feeds.py --runtime /x")
    # Windows tasklist gives only the image name (no argv)
    assert sv.looks_like_relay('"racecast.exe","1234","Console"', windows=True)
    assert sv.looks_like_relay('"python.exe","1234","Console"', windows=True)
    # unrelated processes must NOT match
    assert not sv.looks_like_relay("/usr/bin/vim notes.txt")
    assert not sv.looks_like_relay('"notepad.exe","1234","Console"', windows=True)
    assert not sv.looks_like_relay("")


def t_stop_pid_skips_foreign_pid(tmp):
    # A stale/recycled PID file naming an unrelated live process must NOT be
    # killed: stop_pid drops the pid file and reports gone without signalling.
    pidf = os.path.join(tmp, "relay.pid")
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    pid = sv.start_detached(argv, os.path.join(tmp, "l.log"), pidf)
    try:
        assert sv.pid_alive(pid) is True
        assert sv.stop_pid(pid, pidf, timeout=5, is_target=lambda _p: False) is True
        assert sv.pid_alive(pid) is True          # NOT killed, it was not ours
        assert not os.path.exists(pidf)           # stale pid file cleared
    finally:
        sv.stop_pid(pid, pidf, timeout=5)         # real cleanup


def t_stop_pid_kills_verified_target(tmp):
    pidf = os.path.join(tmp, "relay2.pid")
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    pid = sv.start_detached(argv, os.path.join(tmp, "l2.log"), pidf)
    assert sv.pid_alive(pid) is True
    assert sv.stop_pid(pid, pidf, timeout=5, is_target=lambda _p: True) is True
    assert sv.pid_alive(pid) is False


def t_spawn_kwargs_per_os():
    assert sv.spawn_kwargs("posix") == {"start_new_session": True}
    # Windows daemon spawn: CREATE_NO_WINDOW (0x08000000), NOT DETACHED_PROCESS.
    # A frozen onefile relay is a two-process tree (bootloader -> app); under
    # DETACHED_PROCESS the bootloader has NO console, so the inner app process is
    # given a fresh VISIBLE console that stays open for the whole event. A hidden
    # console (CREATE_NO_WINDOW) is inherited by the inner process instead.
    # CREATE_NEW_PROCESS_GROUP keeps Ctrl+C isolation; the child still outlives us.
    assert sv.spawn_kwargs("nt") == {"creationflags": 0x08000000 | 0x00000200}
    assert sv.spawn_kwargs("java") == {}


def t_no_window_kwargs_per_os():
    # CREATE_NO_WINDOW only on Windows; a no-op (empty kwargs) everywhere else so
    # the same call site stays cross-platform.
    assert sv.no_window_kwargs("nt") == {"creationflags": 0x08000000}
    assert sv.no_window_kwargs("posix") == {}
    assert sv.no_window_kwargs("java") == {}


def t_external_tool_env_not_frozen_inherits():
    # Not frozen -> None, so the caller passes no env= and the child inherits
    # os.environ unchanged (a dev box may set LD_LIBRARY_PATH legitimately).
    assert sv.external_tool_env(frozen=False, environ={"LD_LIBRARY_PATH": "/x"}) is None


def t_external_tool_env_strips_single_meipass():
    # The common single-level case: the bootloader put ONLY its own _MEIPASS on
    # the path -> drop the var so a system-linked yt-dlp/streamlink finds the
    # system libcrypto (the OPENSSL_3.3.0 crash).
    env = sv.external_tool_env(frozen=True, environ={
        "LD_LIBRARY_PATH": "/tmp/_MEIabc", "PATH": "/usr/bin"})
    assert "LD_LIBRARY_PATH" not in env
    assert env["PATH"] == "/usr/bin"          # everything else carried through


def t_external_tool_env_strips_nested_and_parent_meipass():
    # A frozen UI re-invokes the frozen binary, so the child's LD_LIBRARY_PATH
    # carries BOTH _MEIPASS dirs and the bootloader's _ORIG points at the PARENT's
    # _MEIPASS. Restoring _ORIG would reintroduce a bundled libcrypto; stripping
    # every _MEI* dir keeps only the genuinely-external entry. Build the path with
    # os.pathsep so the test matches the splitter on every OS (the code is
    # POSIX-only in practice, but CI runs it on the Windows runner too).
    env = sv.external_tool_env(frozen=True, environ={
        "LD_LIBRARY_PATH": os.pathsep.join(["/tmp/_MEIchild", "/tmp/_MEIparent", "/usr/lib"]),
        "LD_LIBRARY_PATH_ORIG": "/tmp/_MEIparent",   # the trap _ORIG falls into
        "PATH": "/usr/bin"})
    assert env["LD_LIBRARY_PATH"] == "/usr/lib"      # both _MEI dirs gone
    assert env["PATH"] == "/usr/bin"


def t_external_tool_env_strips_active_meipass_by_identity():
    # sys._MEIPASS (this process's bundle dir) is dropped even if its basename
    # somehow does not match _MEI*; identity is the backstop.
    saved = getattr(sys, "_MEIPASS", None)
    sys._MEIPASS = "/opt/bundle/run123"
    try:
        env = sv.external_tool_env(frozen=True, environ={
            "LD_LIBRARY_PATH": os.pathsep.join(["/opt/bundle/run123", "/usr/lib"])})
        assert env["LD_LIBRARY_PATH"] == "/usr/lib"
    finally:
        if saved is None:
            del sys._MEIPASS
        else:
            sys._MEIPASS = saved


def t_external_tool_env_drops_var_when_only_meipass():
    env = sv.external_tool_env(frozen=True, environ={
        "LD_LIBRARY_PATH": "/tmp/_MEIabc",
        "DYLD_LIBRARY_PATH": os.pathsep.join(["/tmp/_MEIabc", "/tmp/_MEIxyz"]),
        "PATH": "/usr/bin"})
    assert "LD_LIBRARY_PATH" not in env
    assert "DYLD_LIBRARY_PATH" not in env
    assert env["PATH"] == "/usr/bin"


def t_external_tool_env_does_not_mutate_input():
    src = {"LD_LIBRARY_PATH": "/tmp/_MEIabc:/usr/lib"}
    sv.external_tool_env(frozen=True, environ=src)
    assert src["LD_LIBRARY_PATH"] == "/tmp/_MEIabc:/usr/lib"   # caller's dict untouched


def t_start_detached_uses_boot_log_when_given(tmp):
    boot = os.path.join(tmp, "logs", "relay.boot.log")
    pidf = os.path.join(tmp, "relay.pid")
    # The child crashes to stderr; start_detached must capture that to the boot file
    # the caller passes (the "boot file" contract, so pre-logging crashes are visible).
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n')"]
    pid = sv.start_detached(argv, boot, pidf)
    # Let the short-lived child run to completion before we stop/clean up, so the
    # stderr write reaches the boot file (no race against an immediate signal).
    for _ in range(50):
        if not sv.pid_alive(pid):
            break
        time.sleep(0.1)
    sv.stop_pid(pid, pidf, timeout=5)
    assert os.path.exists(boot)             # crash/stderr captured to the boot file
    with open(boot, encoding="utf-8") as fh:
        assert "boom" in fh.read()


def t_tail_merged_prefixes_sources(tmp):
    import io, contextlib
    a = os.path.join(tmp, "feed_A.log"); b = os.path.join(tmp, "feed_B.log")
    with open(a, "w", encoding="utf-8") as fh:
        fh.write("a-line\n")
    with open(b, "w", encoding="utf-8") as fh:
        fh.write("b-line\n")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sv.tail_merged([a, b], follow=False, lines=10)
    out = buf.getvalue()
    assert "[feed_A] a-line" in out and "[feed_B] b-line" in out


def t_tail_merged_honors_lines_limit(tmp):
    import io, contextlib
    a = os.path.join(tmp, "feed_A.log")
    with open(a, "w", encoding="utf-8") as fh:
        fh.write("".join(f"line-{i}\n" for i in range(10)))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sv.tail_merged([a], follow=False, lines=3)
    out = buf.getvalue()
    assert "line-6" not in out                       # trimmed to the last 3
    assert out.count("[feed_A]") == 3
    assert "[feed_A] line-7" in out and "[feed_A] line-9" in out


def t_stop_commands_per_os():
    assert sv.stop_commands("posix", 123, force=False) is None
    assert sv.stop_commands("posix", 123, force=True) is None
    assert sv.stop_commands("nt", 123, force=False) == ["taskkill", "/PID", "123"]
    assert sv.stop_commands("nt", 123, force=True) == \
        ["taskkill", "/F", "/T", "/PID", "123"]


def t_daemon_bundle_env_redirects_the_extraction_dir_posix():
    # A frozen daemon runs for days; /tmp gets reaped under it. Point the
    # bootloader at a durable dir instead. It is free here, because the child is
    # starting anyway and unpacks exactly once either way.
    env = sv.daemon_bundle_env({"PATH": "/usr/bin", "TMPDIR": "/var/folders/x/T"},
                               "/opt/rc/runtime/bundle", os_name="posix")
    assert env["TMPDIR"] == "/opt/rc/runtime/bundle"
    assert env["PATH"] == "/usr/bin"          # everything else untouched
    assert "TEMP" not in env


def t_daemon_bundle_env_sets_both_windows_vars():
    env = sv.daemon_bundle_env({"TEMP": r"C:\Users\x\AppData\Local\Temp"},
                               r"C:\rc\runtime\bundle", os_name="nt")
    assert env["TEMP"] == r"C:\rc\runtime\bundle"
    assert env["TMP"] == r"C:\rc\runtime\bundle"


def t_daemon_bundle_env_without_a_dir_is_a_no_op():
    src = {"TMPDIR": "/var/folders/x/T"}
    assert sv.daemon_bundle_env(src, "", os_name="posix") == src
    assert sv.daemon_bundle_env(src, None, os_name="posix") == src


def t_daemon_bundle_env_does_not_mutate_the_input():
    src = {"TMPDIR": "/old"}
    out = sv.daemon_bundle_env(src, "/new", os_name="posix")
    assert src == {"TMPDIR": "/old"}, src
    assert out["TMPDIR"] == "/new"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in sorted(globals().items()):
            if name.startswith("t_") and callable(fn):
                import inspect
                fn(tmp) if inspect.signature(fn).parameters else fn()
                print("ok", name)
    print("ALL PASS")

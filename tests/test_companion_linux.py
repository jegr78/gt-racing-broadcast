#!/usr/bin/env python3
"""Stdlib unit checks for the Linux companion-pi control helpers.
Run: python3 tests/test_companion_linux.py"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import companion_linux as cl


# is_valid_bind_ip.
def t_valid_ipv4():
    assert cl.is_valid_bind_ip("100.64.10.20") is True
    assert cl.is_valid_bind_ip("127.0.0.1") is True
    assert cl.is_valid_bind_ip("0.0.0.0") is True   # explicit operator override is allowed


def t_valid_ipv6():
    assert cl.is_valid_bind_ip("::1") is True


def t_invalid_ip_rejected():
    assert cl.is_valid_bind_ip("not-an-ip") is False
    assert cl.is_valid_bind_ip("100.64.10.20; rm -rf /") is False
    assert cl.is_valid_bind_ip("") is False


# bind_env_content.
def t_bind_env_content_writes_var():
    assert cl.bind_env_content("100.64.10.20") == "RACECAST_ADMIN_ADDRESS=100.64.10.20\n"


def t_bind_env_content_rejects_bad_ip():
    raised = False
    try:
        cl.bind_env_content("$(reboot)")
    except ValueError:
        raised = True
    assert raised


# bind_dropin_content.
def t_dropin_resets_and_sets_execstart_with_admin_address():
    c = cl.bind_dropin_content()
    assert "[Service]\n" in c
    assert f"EnvironmentFile=-{cl.BIND_ENV}\n" in c
    assert "ExecStart=\n" in c                       # reset the vendor ExecStart first
    assert "--admin-address \"${RACECAST_ADMIN_ADDRESS:-127.0.0.1}\"" in c
    assert "/opt/companion/main.js" in c
    assert "--extra-module-path /opt/companion-module-dev" in c


# sudoers_dropin_content.
def t_sudoers_content_narrow_nopasswd():
    c = cl.sudoers_dropin_content("jegr", "/usr/bin/systemctl")
    assert c.endswith(
        "jegr ALL=(root) NOPASSWD: /usr/local/sbin/racecast-companion-bind, "
        "/usr/bin/systemctl stop companion\n")


# bind_helper_content.
def t_helper_validates_and_restarts():
    c = cl.bind_helper_content()
    assert c.startswith("#!/bin/bash\n")
    assert "systemctl restart companion" in c
    assert cl.BIND_ENV in c
    assert "exit 2" in c                             # rejects a bad ip argument
    assert "set -euo pipefail" in c
    assert "grep -Eq" in c


def t_helper_ipv6_branch_requires_colon():
    # The bash IPv6 alternative must require at least one ':' so colon-less hex
    # (e.g. "deadbeef") is rejected, matching the Python ipaddress check.
    c = cl.bind_helper_content()
    assert "[0-9A-Fa-f:]+$" not in c        # the loose pattern must be gone
    assert ":[0-9A-Fa-f:]" in c             # a colon is now required in the IPv6 branch


# control_commands.
def t_control_commands_shape():
    c = cl.control_commands("companion")
    assert c["start"] == ["sudo", "-n", cl.HELPER_PATH]      # caller appends the ip
    assert c["quit"] == ["sudo", "-n", "systemctl", "stop", "companion"]
    assert c["running"] == ["systemctl", "is-active", "companion"]


# detect_unit.
def t_detect_unit_none_on_windows_or_macos():
    assert cl.detect_unit(platform="win32") is None
    assert cl.detect_unit(platform="darwin") is None


def t_detect_unit_none_without_systemctl():
    assert cl.detect_unit(platform="linux", which=lambda n: None) is None


def t_detect_unit_via_systemctl_cat():
    class P:
        returncode = 0
    got = cl.detect_unit(platform="linux", which=lambda n: "/usr/bin/" + n,
                         run=lambda *a, **k: P(), exists=lambda p: False)
    assert got == "companion"


def t_detect_unit_via_unit_file_when_cat_fails():
    class P:
        returncode = 1
    got = cl.detect_unit(platform="linux", which=lambda n: "/usr/bin/" + n,
                         run=lambda *a, **k: P(),
                         exists=lambda p: p == cl.SERVICE_UNIT_FILE)
    assert got == "companion"


def t_detect_unit_none_when_absent():
    class P:
        returncode = 1
    assert cl.detect_unit(platform="linux", which=lambda n: "/usr/bin/" + n,
                          run=lambda *a, **k: P(), exists=lambda p: False) is None


# is_enabled, and its idempotency.
def _reader_for(files):
    return files.get


def t_is_enabled_true_when_all_match():
    files = {
        cl.DROPIN_CONF: cl.bind_dropin_content(),
        cl.HELPER_PATH: cl.bind_helper_content(),
        cl.SUDOERS_PATH: cl.sudoers_dropin_content("jegr", "/usr/bin/systemctl"),
    }
    assert cl.is_enabled(_reader_for(files), "jegr", "/usr/bin/systemctl") is True


def t_is_enabled_false_when_missing():
    assert cl.is_enabled(_reader_for({}), "jegr", "/usr/bin/systemctl") is False


def t_is_enabled_false_when_sudoers_user_differs():
    files = {
        cl.DROPIN_CONF: cl.bind_dropin_content(),
        cl.HELPER_PATH: cl.bind_helper_content(),
        cl.SUDOERS_PATH: cl.sudoers_dropin_content("other", "/usr/bin/systemctl"),
    }
    assert cl.is_enabled(_reader_for(files), "jegr", "/usr/bin/systemctl") is False


# enable_control.
class _FakeRun:
    """Records argv and returns a scripted returncode per matched command."""
    def __init__(self, rc_for=None):
        self.calls = []
        self.rc_for = rc_for or {}
    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc = 0
        for needle, code in self.rc_for.items():
            if needle in " ".join(argv):
                rc = code
        class P:
            returncode = rc
            stdout = ""
            stderr = ""
        return P()


def _cmds(fake):
    return [" ".join(c) for c in fake.calls]


def t_enable_control_unsupported_returns_nonzero():
    run = _FakeRun()
    rc = cl.enable_control(platform="darwin", run=run, which=lambda n: "/usr/bin/" + n,
                           getuser=lambda: "jegr", read_text=lambda p: None,
                           write_temp=lambda c: "/tmp/x", log=lambda *a: None)
    assert rc != 0
    assert run.calls == []          # nothing privileged attempted off Linux


def t_enable_control_already_enabled_is_noop():
    files = {
        cl.DROPIN_CONF: cl.bind_dropin_content(),
        cl.HELPER_PATH: cl.bind_helper_content(),
        cl.SUDOERS_PATH: cl.sudoers_dropin_content("jegr", "/usr/bin/systemctl"),
    }
    run = _FakeRun()
    rc = cl.enable_control(platform="linux", run=run, which=lambda n: "/usr/bin/" + n,
                           getuser=lambda: "jegr", read_text=files.get,
                           write_temp=lambda c: "/tmp/x", exists=lambda p: False,
                           log=lambda *a: None)
    assert rc == 0
    # detect_unit may probe systemctl, but NO install/visudo/daemon-reload writes:
    assert not any("install" in c or "visudo" in c for c in _cmds(run))


def t_enable_control_happy_path_installs_and_validates():
    run = _FakeRun()    # all rc 0 incl. systemctl is-active -> active
    rc = cl.enable_control(platform="linux", run=run, which=lambda n: "/usr/bin/" + n,
                           getuser=lambda: "jegr", read_text=lambda p: None,
                           write_temp=lambda c: "/tmp/stage", exists=lambda p: False,
                           log=lambda *a: None)
    cmds = _cmds(run)
    assert any("visudo -cf" in c for c in cmds)                       # validate sudoers
    assert any("install" in c and cl.HELPER_PATH in c for c in cmds)  # helper placed
    assert any("install" in c and cl.DROPIN_CONF in c for c in cmds)  # drop-in placed
    assert any("install" in c and cl.SUDOERS_PATH in c for c in cmds) # sudoers placed
    assert any("daemon-reload" in c for c in cmds)
    assert any("is-active" in c for c in cmds)                        # post-install probe
    assert rc == 0


def t_enable_control_rolls_back_when_service_fails_to_start():
    # is-active returns non-zero -> validation fails -> rollback
    run = _FakeRun(rc_for={"is-active": 3})
    rc = cl.enable_control(platform="linux", run=run, which=lambda n: "/usr/bin/" + n,
                           getuser=lambda: "jegr", read_text=lambda p: None,
                           write_temp=lambda c: "/tmp/stage", exists=lambda p: False,
                           log=lambda *a: None)
    cmds = _cmds(run)
    assert any("rm" in c and cl.DROPIN_CONF in c for c in cmds)   # drop-in removed
    assert any("rm" in c and cl.HELPER_PATH in c and cl.SUDOERS_PATH in c for c in cmds)
    assert any("daemon-reload" in c for c in cmds)
    assert any("restart companion" in c for c in cmds)
    assert rc != 0


def t_enable_control_visudo_failure_aborts_with_no_installs():
    run = _FakeRun(rc_for={"visudo": 1})
    rc = cl.enable_control(platform="linux", run=run, which=lambda n: "/usr/bin/" + n,
                           getuser=lambda: "jegr", read_text=lambda p: None,
                           write_temp=lambda c: "/tmp/x", exists=lambda p: False,
                           log=lambda *a: None)
    cmds = _cmds(run)
    assert rc != 0
    assert any("visudo" in c for c in cmds)
    assert not any("install" in c for c in cmds)
    assert not any("daemon-reload" in c for c in cmds)


# Frozen-binary env scrubbing for the bare `systemctl` calls. The default run
# must hand subprocess.run the de-PyInstaller'd env: under the frozen binary a
# bare `systemctl` (is-active and cat, the non-sudo ones) would otherwise inherit
# _MEIPASS on LD_LIBRARY_PATH, load our bundled libcrypto and exit non-zero
# ("OPENSSL_x.y.z not found"), so enable-control rolls back a service that
# actually started and `companion status` reports a false "stopped".
class _RecordingSub:
    def __init__(self):
        self.calls = []
    def run(self, argv, **kw):
        self.calls.append((argv, kw.get("env", "MISSING")))
        class P:
            returncode = 0
            stdout = ""
        return P()


def _with_default_run(env_value, body):
    rec = _RecordingSub()
    orig_sub, orig_env = cl.subprocess, cl.external_tool_env
    cl.subprocess = rec
    cl.external_tool_env = lambda: env_value
    try:
        body()
    finally:
        cl.subprocess, cl.external_tool_env = orig_sub, orig_env
    return rec


def t_default_run_passes_scrubbed_env_when_frozen():
    scrubbed = {"LD_LIBRARY_PATH": "/usr/lib/aarch64-linux-gnu"}
    rec = _with_default_run(
        scrubbed,
        lambda: cl._default_run(["systemctl", "is-active", "companion"]))
    assert rec.calls[0][0] == ["systemctl", "is-active", "companion"]
    assert rec.calls[0][1] == scrubbed


def t_default_run_env_none_when_not_frozen():
    # external_tool_env() returns None off the frozen binary -> inherit os.environ.
    rec = _with_default_run(
        None, lambda: cl._default_run(["systemctl", "cat", "companion"]))
    assert rec.calls[0][1] is None


def t_default_run_keeps_caller_kwargs():
    rec = _with_default_run(
        {"X": "1"},
        lambda: cl._default_run(["systemctl", "is-active", "companion"],
                                capture_output=True, text=True))
    # env injected without dropping the caller's kwargs (recorded env present).
    assert rec.calls[0][1] == {"X": "1"}


def t_enable_control_refuses_2x_layout():
    run = _FakeRun()
    rc = cl.enable_control(platform="linux", run=run, which=lambda n: "/usr/bin/" + n,
                           getuser=lambda: "jegr", read_text=lambda p: None,
                           write_temp=lambda c: "/tmp/x",
                           exists=lambda p: p == cl.LEGACY_2X_DIR, log=lambda *a: None)
    assert rc != 0
    assert not any("install" in c for c in _cmds(run))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

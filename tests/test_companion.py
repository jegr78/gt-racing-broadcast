#!/usr/bin/env python3
"""Stdlib unit checks for the Companion start/stop bind helpers.
Run: python3 tests/test_companion.py"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import companion_common as cc


# desired_bind_ip: one bind value for Companion, auto -> Tailscale.
def t_desired_auto_uses_tailscale_when_present():
    assert cc.desired_bind_ip("auto", "100.64.10.20") == "100.64.10.20"


def t_desired_auto_falls_back_to_localhost():
    assert cc.desired_bind_ip("auto", None) == "127.0.0.1"


def t_desired_explicit_overrides_detection():
    assert cc.desired_bind_ip("0.0.0.0", "100.64.10.20") == "0.0.0.0"
    assert cc.desired_bind_ip("127.0.0.1", None) == "127.0.0.1"


# config_with_bind_ip: edit only bind_ip, keep every other key.
def t_config_updates_bind_ip_preserving_other_keys():
    src = '{"bind_ip": "127.0.0.1", "http_port": 8000, "syslog_port": 514}'
    d = json.loads(cc.config_with_bind_ip(src, "100.64.10.20"))
    assert d["bind_ip"] == "100.64.10.20"
    assert d["http_port"] == 8000 and d["syslog_port"] == 514


def t_config_invalid_json_raises_valueerror():
    raised = False
    try:
        cc.config_with_bind_ip("{ not json", "100.64.10.20")
    except ValueError:
        raised = True
    assert raised


# plan_companion_action: the stop-before-edit, start-at-end decision.
def t_plan_change_while_running_stops_first():
    assert cc.plan_companion_action("127.0.0.1", "100.64.10.20", True) == \
        {"edit": True, "stop_first": True, "start": True}


def t_plan_change_while_stopped_no_stop():
    assert cc.plan_companion_action("127.0.0.1", "100.64.10.20", False) == \
        {"edit": True, "stop_first": False, "start": True}


def t_plan_already_correct_and_running_is_noop():
    assert cc.plan_companion_action("100.64.10.20", "100.64.10.20", True) == \
        {"edit": False, "stop_first": False, "start": False}


def t_plan_already_correct_but_stopped_starts():
    assert cc.plan_companion_action("100.64.10.20", "100.64.10.20", False) == \
        {"edit": False, "stop_first": False, "start": True}


# companion_config_path: the per-OS location of config.json.
def t_config_path_macos():
    # normalize separators: expanduser and join mix / and \ on Windows
    p = cc.companion_config_path("darwin", env={}).replace(os.sep, "/")
    assert p.endswith("Library/Application Support/companion/config.json"), p


def t_config_path_linux_xdg():
    # built with os.path.join, since the separators differ on Windows
    assert cc.companion_config_path("linux", env={"XDG_CONFIG_HOME": "/x/cfg"}) == \
        os.path.join("/x/cfg", "companion", "config.json")


def t_config_path_windows_appdata():
    p = cc.companion_config_path("win32", env={"APPDATA": r"C:\Roaming"})
    assert p == os.path.join(r"C:\Roaming", "companion", "config.json")


# companion_control_commands: macOS start, quit and running, else None.
def t_control_commands_macos():
    c = cc.companion_control_commands("darwin")
    assert c["start"][0] == "open" and c["quit"][0] == "osascript" and "running" in c


def t_control_commands_unsupported_is_none():
    assert cc.companion_control_commands("sunos5") is None


def t_control_commands_windows():
    exe = os.path.join("C:" + os.sep, "Apps", "Companion.exe")
    cmds = cc.companion_control_commands("win32", exe=exe)
    assert cmds["start"] == [exe]
    assert cmds["quit"] == ["taskkill", "/IM", "Companion.exe"]
    assert cmds["running"] == ["tasklist", "/FI", "IMAGENAME eq Companion.exe"]


def t_control_commands_windows_requires_exe():
    assert cc.companion_control_commands("win32", exe=None) is None


def t_control_commands_linux_is_manual():
    assert cc.companion_control_commands("linux") is None


def t_control_commands_darwin_unchanged():
    cmds = cc.companion_control_commands("darwin")
    assert cmds["start"] == ["open", "-a", "Companion"]


def t_find_companion_exe_override_wins():
    path = os.path.join("D:" + os.sep, "Tools", "Companion.exe")
    assert cc.find_companion_exe({"RACECAST_COMPANION_EXE": path}, exists=lambda p: True) == path
    assert cc.find_companion_exe({"RACECAST_COMPANION_EXE": path}, exists=lambda p: False) is None


def t_find_companion_exe_candidates():
    local = os.path.join("C:" + os.sep, "Users", "x", "AppData", "Local")
    hit = local + r"\Programs\companion\Companion.exe"
    assert cc.find_companion_exe({"LOCALAPPDATA": local}, exists=lambda p: p == hit) == hit
    assert cc.find_companion_exe({}, exists=lambda p: False) is None


def t_parse_running():
    assert cc.parse_running("win32", 0, "INFO: No tasks are running ...") is False
    assert cc.parse_running("win32", 0, '"Companion.exe","4242","Console"') is True
    assert cc.parse_running("darwin", 0, "") is True
    assert cc.parse_running("darwin", 1, "") is False


# companion_reachable, the TCP health probe.
def t_companion_reachable():
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert cc.companion_reachable("127.0.0.1", port, 1.0) is True
    finally:
        srv.close()
    # A closed port gives False rather than an exception.
    assert cc.companion_reachable("127.0.0.1", port, 0.5) is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

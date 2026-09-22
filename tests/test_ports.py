#!/usr/bin/env python3
"""Stdlib checks for the cross-platform port helpers (racecast freeport). Run:
python3 tests/test_ports.py"""
import importlib.util, os, signal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ports = _load("ports", os.path.join("src", "scripts", "ports.py"))


# Pure parsers.

def t_parse_lsof_pids():
    assert ports.parse_lsof_pids("4321\n4322\n") == [4321, 4322]
    assert ports.parse_lsof_pids("") == []
    assert ports.parse_lsof_pids("  \n") == []
    # dedupes and sorts, ignoring non-numeric noise
    assert ports.parse_lsof_pids("9\n9\n3\noops\n") == [3, 9]


def t_parse_ss_pids():
    line = ('LISTEN 0 128 0.0.0.0:53001 0.0.0.0:* '
            'users:(("streamlink",pid=1234,fd=3))')
    assert ports.parse_ss_pids(line) == [1234]
    assert ports.parse_ss_pids("LISTEN 0 128 *:53001 *:*\n") == []  # no pid= -> none


def t_parse_fuser_pids():
    # fuser prints "53001/tcp:  1234 5678"
    assert ports.parse_fuser_pids("53001/tcp:  1234 5678\n") == [1234, 5678]
    assert ports.parse_fuser_pids("53001/tcp:\n") == []
    assert ports.parse_fuser_pids("") == []


def t_parse_netstat_pids_windows():
    out = (
        "\n"
        "Active Connections\n"
        "\n"
        "  Proto  Local Address          Foreign Address        State           PID\n"
        "  TCP    0.0.0.0:53001          0.0.0.0:0              LISTENING       4321\n"
        "  TCP    [::]:53001             [::]:0                 LISTENING       4321\n"
        "  TCP    127.0.0.1:53002        0.0.0.0:0              LISTENING       8888\n"
        "  TCP    127.0.0.1:9999         127.0.0.1:53001        ESTABLISHED     5555\n"  # client TO :53001
    )
    assert ports.parse_netstat_pids(out, 53001) == [4321]   # only LISTENING on local :53001
    assert ports.parse_netstat_pids(out, 53002) == [8888]
    assert ports.parse_netstat_pids(out, 53003) == []
    # a port that is only a numeric prefix must not match: ":5300" is not ":53001"
    assert ports.parse_netstat_pids(out, 5300) == []


def t_parse_netstat_pids_localized_state_column():
    # Non-English Windows localizes the State column: German prints 'ABHÖREN' for
    # LISTENING and 'WARTEND' for TIME_WAIT. The parser must key on the wildcard
    # foreign address, never the localized word, or pids_on_port returns [] on every
    # non-English host.
    out = (
        "  TCP    127.0.0.1:8088         0.0.0.0:0              ABHÖREN         34528\n"
        "  TCP    100.115.69.85:8088     0.0.0.0:0              ABHÖREN         34528\n"
        "  TCP    127.0.0.1:8088         127.0.0.1:49568        WARTEND         0\n"
        "  TCP    100.115.69.85:8088     100.115.69.85:49434    WARTEND         0\n"
    )
    assert ports.parse_netstat_pids(out, 8088) == [34528], \
        "only the two wildcard-foreign LISTENING rows count, deduped, and WARTEND is ignored"


# pids_on_port, with an injected command runner and which.

def t_pids_on_port_posix_prefers_lsof():
    calls = []
    def run(argv):
        calls.append(argv)
        return "4321\n"
    pids = ports.pids_on_port(53001, os_name="posix", run=run, which=lambda x: x == "lsof" and "/usr/bin/lsof")
    assert pids == [4321]
    assert any("lsof" in a[0] for a in calls)
    assert all("ss" not in a[0] for a in calls)  # lsof found -> no ss fallback


def t_pids_on_port_posix_falls_back_to_ss():
    seen = []
    def run(argv):
        seen.append(argv[0])
        if argv[0] == "ss":
            return 'LISTEN 0 128 0.0.0.0:53001 0.0.0.0:* users:(("streamlink",pid=77,fd=3))'
        return ""
    avail = {"ss"}  # no lsof, yes ss
    pids = ports.pids_on_port(53001, os_name="posix", run=run,
                              which=lambda x: ("/bin/" + x) if x in avail else None)
    assert pids == [77]
    assert "ss" in seen


def t_pids_on_port_windows_uses_netstat():
    out = "  TCP    0.0.0.0:53003   0.0.0.0:0   LISTENING   2222\n"
    pids = ports.pids_on_port(53003, os_name="nt", run=lambda argv: out, which=lambda x: x)
    assert pids == [2222]


# decide_free, the pure safety gate.

def t_decide_free_no_pids_is_clear():
    assert ports.decide_free([], owned=False, force=False)[0] == "clear"


def t_decide_free_refuses_running_service():
    action, pids = ports.decide_free([99], owned=True, force=False)
    assert action == "refuse"
    assert pids == [99]


def t_decide_free_force_overrides_owned():
    assert ports.decide_free([99], owned=True, force=True)[0] == "free"


def t_decide_free_kills_orphan():
    assert ports.decide_free([99], owned=False, force=False)[0] == "free"


# kill_pid, on injected seams, with no real processes.

def t_kill_pid_posix_term_then_kill():
    if os.name == "nt":
        return  # SIGKILL is POSIX-only; the nt branch (taskkill) is covered separately
    killed = []
    calls = []
    state = {"alive": True}
    def kill(pid, sig):
        killed.append((pid, sig))
        if sig == signal.SIGKILL:
            state["alive"] = False
    ports.kill_pid(4321, os_name="posix",
                   call=lambda *a, **k: calls.append(a[0]),
                   kill=kill, sleep=lambda _s: None,
                   alive=lambda _p: state["alive"])
    assert (4321, signal.SIGTERM) in killed
    assert (4321, signal.SIGKILL) in killed         # escalates while still alive
    assert any("pkill" in c[0] for c in calls)      # reaps direct children first


def t_kill_pid_posix_no_kill_signal_when_term_works():
    if os.name == "nt":
        return  # references signal.SIGKILL (POSIX-only)
    killed = []
    ports.kill_pid(4321, os_name="posix", call=lambda *a, **k: None,
                   kill=lambda p, s: killed.append((p, s)),
                   sleep=lambda _s: None, alive=lambda _p: False)  # dies on TERM
    assert (4321, signal.SIGTERM) in killed
    assert (4321, signal.SIGKILL) not in killed


def t_kill_pid_windows_taskkill_tree():
    calls = []
    ports.kill_pid(4321, os_name="nt", call=lambda *a, **k: calls.append(a[0]))
    assert calls and calls[0][:2] == ["taskkill", "/PID"]
    assert "/T" in calls[0] and "/F" in calls[0]


def t_feed_ports_default():
    assert ports.FEED_PORTS == (53001, 53002, 53003)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

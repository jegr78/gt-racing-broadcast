#!/usr/bin/env python3
"""Stdlib unit checks for the Tailscale detection/control helpers.
Run: python3 tests/test_tailscale.py"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import tailscale as ts


def _status_json(state, ips):
    return json.dumps({"BackendState": state, "Self": {"TailscaleIPs": ips}})


# _in_cgnat: Tailscale uses the 100.64.0.0/10 CGNAT range.
def t_cgnat_range():
    assert ts._in_cgnat("100.64.10.20") is True
    assert ts._in_cgnat("100.63.255.255") is False
    assert ts._in_cgnat("192.168.1.5") is False
    assert ts._in_cgnat("not-an-ip") is False


# parse_tailscale_backend: (BackendState, ip) from `status --json`.
def t_backend_running_returns_state_and_ip():
    out = _status_json("Running", ["fd7a:115c:a1e0::1", "100.64.10.20"])
    assert ts.parse_tailscale_backend(out) == ("Running", "100.64.10.20")


def t_backend_stopped_keeps_state_but_no_ip():
    # A disconnected node keeps its assigned tailnet IP, so never report it.
    out = _status_json("Stopped", ["100.64.10.20"])
    assert ts.parse_tailscale_backend(out) == ("Stopped", None)


def t_backend_needslogin():
    assert ts.parse_tailscale_backend(_status_json("NeedsLogin", [])) == \
        ("NeedsLogin", None)


def t_backend_running_without_cgnat_ip():
    assert ts.parse_tailscale_backend(_status_json("Running", [])) == ("Running", None)


def t_backend_garbage_is_none_none():
    assert ts.parse_tailscale_backend("") == (None, None)
    assert ts.parse_tailscale_backend("not json") == (None, None)
    assert ts.parse_tailscale_backend("[1, 2]") == (None, None)
    assert ts.parse_tailscale_backend('{"Self": {}}') == (None, None)


# parse_tailscale_status: the Running IP only, a detection compat wrapper.
def t_status_wrapper_running_vs_stopped():
    assert ts.parse_tailscale_status(_status_json("Running", ["100.64.10.20"])) == \
        "100.64.10.20"
    assert ts.parse_tailscale_status(_status_json("Stopped", ["100.64.10.20"])) is None
    assert ts.parse_tailscale_status(_status_json("NeedsLogin", ["100.64.10.20"])) is None


# plan_tailscale_up: the decision for an `up` request given a BackendState.
def t_plan_running_is_connected():
    assert ts.plan_tailscale_up("Running") == "connected"


def t_plan_stopped_and_starting_run_up():
    assert ts.plan_tailscale_up("Stopped") == "run-up"
    assert ts.plan_tailscale_up("Starting") == "run-up"
    assert ts.plan_tailscale_up("NoState") == "run-up"


def t_plan_login_states_never_run_up():
    # `up` in these states would trigger the interactive browser login.
    assert ts.plan_tailscale_up("NeedsLogin") == "needs-login"
    assert ts.plan_tailscale_up("NeedsMachineAuth") == "needs-login"


def t_plan_no_backend_launches_app():
    assert ts.plan_tailscale_up(None) == "launch-app"


# parse_tailscale_peers: the tailnet device list for the takeover dropdown.
def t_parse_peers_extracts_hostname_ip_online_os():
    data = {"Peer": {
        "k1": {"HostName": "producer-b", "TailscaleIPs": ["100.64.0.5", "fd7a::1"],
               "Online": True, "OS": "macOS"},
        "k2": {"HostName": "tablet", "TailscaleIPs": ["100.64.0.9"],
               "Online": False, "OS": "iOS"},
        "k3": {"HostName": "no-cgnat", "TailscaleIPs": ["fd7a::2"],
               "Online": True, "OS": "linux"},          # no CGNAT IPv4 -> skipped
    }}
    peers = ts.parse_tailscale_peers(json.dumps(data))
    assert {"hostname": "producer-b", "ip": "100.64.0.5", "online": True, "os": "macOS"} in peers
    assert {"hostname": "tablet", "ip": "100.64.0.9", "online": False, "os": "iOS"} in peers
    assert all(p["hostname"] != "no-cgnat" for p in peers)
    assert len(peers) == 2


def t_parse_peers_garbage_and_empty():
    assert ts.parse_tailscale_peers("not json") == []
    assert ts.parse_tailscale_peers(json.dumps({})) == []           # no Peer map
    assert ts.parse_tailscale_peers(json.dumps({"Peer": {}})) == []
    assert ts.parse_tailscale_peers(json.dumps({"Peer": None})) == []


def t_parse_funnel_serving():
    on = ("https://rig.tail1234.ts.net (Funnel on)\n"
          "|-- /console proxy http://127.0.0.1:8088/console\n")
    # The default path is /console. (#216)
    assert ts.parse_funnel_serving(on) is True
    assert ts.parse_funnel_serving(on, "/console") is True
    # Still parameterizable. An explicit foreign path is not matched.
    assert ts.parse_funnel_serving(on, "/cockpit") is False
    assert ts.parse_funnel_serving("nothing here") is False
    assert ts.parse_funnel_serving("") is False


def t_parse_funnel_capable():
    full = "https://tailscale.com/cap/funnel"
    ports = "https://tailscale.com/cap/funnel-ports?ports=443,8443,10000"
    for key in (full, ports, "funnel"):     # versions vary; accept all forms
        assert ts.parse_funnel_capable(json.dumps({"Self": {"CapMap": {key: []}}})) is True, key
    assert ts.parse_funnel_capable(json.dumps(
        {"Self": {"CapMap": {"https://tailscale.com/cap/ssh": []}}})) is False
    assert ts.parse_funnel_capable(json.dumps({"Self": {}})) is False
    assert ts.parse_funnel_capable("not json") is False


def t_parse_magicdns_name():
    out = json.dumps({"Self": {"DNSName": "rig.tail1234.ts.net."}})
    assert ts.parse_magicdns_name(out) == "rig.tail1234.ts.net"   # trailing dot stripped
    assert ts.parse_magicdns_name(json.dumps({"Self": {}})) == ""
    assert ts.parse_magicdns_name("not json") == ""


def t_funnel_args():
    on = ts.funnel_args(path="/console", target_port=8088, enable=True)
    assert on == ["funnel", "--bg", "--set-path=/console",
                  "http://127.0.0.1:8088/console"]
    # Teardown ignores path and port and resets the funnel config wholesale. The
    # path-specific `--set-path=… off` form fails with "handler does not exist",
    # so `funnel reset` is the only form that tears down across the versions we
    # target. (#200)
    off = ts.funnel_args(path="/console", target_port=8088, enable=False)
    assert off == ["funnel", "reset"]


def t_funnel_args_mounts_only_console():
    # Boundary invariant: the public Funnel exposes ONLY /console. The enable
    # argv must mount exactly one path-prefix, that prefix must be /console, the
    # reverse-proxy target must stay under /console, and nothing may mount the
    # root ("/") or the old /cockpit prefix, so root control endpoints stay
    # unreachable from the public internet. (#216)
    argv = ts.funnel_args(path="/console", target_port=8088, enable=True)
    set_paths = [a for a in argv if a.startswith("--set-path=")]
    assert set_paths == ["--set-path=/console"], set_paths
    assert argv[-1] == "http://127.0.0.1:8088/console"
    assert not any(a == "--set-path=/" or a.endswith("=/cockpit")
                   or a.rstrip("/").endswith("/cockpit") for a in argv)
    assert "/cockpit" not in " ".join(argv)


def t_status_snapshot_text_shape():
    out = ts.status_snapshot_text("100.64.0.1  myhost  active", ts="2026-06-18 12:00:00")
    assert out.startswith("==== 2026-06-18 12:00:00 ====\n")
    assert out.rstrip().endswith("100.64.0.1  myhost  active")
    assert out.endswith("\n")


# magicdns_is_self: the exact-FQDN takeover self-guard.
def t_magicdns_is_self_exact_fqdn():
    me = "producer-b.tail1234.ts.net"
    assert ts.magicdns_is_self("producer-b.tail1234.ts.net", me) is True
    assert ts.magicdns_is_self("PRODUCER-B.TAIL1234.TS.NET", me) is True   # case-insensitive
    assert ts.magicdns_is_self("producer-b.tail1234.ts.net.", me) is True  # trailing dot
    assert ts.magicdns_is_self("  producer-b.tail1234.ts.net  ", me) is True


def t_magicdns_is_self_short_name_does_not_match():
    me = "producer-b.tail1234.ts.net"
    assert ts.magicdns_is_self("producer-b", me) is False        # bare host: exact-FQDN policy
    assert ts.magicdns_is_self("producer-a.tail1234.ts.net", me) is False


def t_magicdns_is_self_unknown_self_is_false():
    assert ts.magicdns_is_self("producer-b.tail1234.ts.net", "") is False
    assert ts.magicdns_is_self("producer-b.tail1234.ts.net", None) is False
    assert ts.magicdns_is_self("", "producer-b.tail1234.ts.net") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

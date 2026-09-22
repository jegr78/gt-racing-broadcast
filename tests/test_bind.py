#!/usr/bin/env python3
"""Stdlib unit checks for the relay's auto dual-bind (localhost + Tailscale IP).
Run: python3 tests/test_bind.py"""
import importlib.util, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


# _in_cgnat: Tailscale uses the 100.64.0.0/10 CGNAT range.
def t_cgnat_typical_tailscale_ip():
    assert m._in_cgnat("100.64.10.20") is True


def t_cgnat_range_edges():
    assert m._in_cgnat("100.64.0.0") is True       # bottom of /10
    assert m._in_cgnat("100.127.255.255") is True  # top of /10
    assert m._in_cgnat("100.63.255.255") is False  # just below
    assert m._in_cgnat("100.128.0.0") is False     # just above


def t_cgnat_rejects_lan_and_garbage():
    assert m._in_cgnat("192.168.1.5") is False
    assert m._in_cgnat("10.0.0.1") is False
    assert m._in_cgnat("not-an-ip") is False
    assert m._in_cgnat("") is False


# parse_tailscale_status takes the CGNAT IPv4 from `tailscale status --json`, but
# only while the backend is Running. A stopped node keeps its assigned IP, so
# `tailscale ip -4` alone reports false positives.
def _status_json(state, ips):
    return json.dumps({"BackendState": state, "Self": {"TailscaleIPs": ips}})


def t_status_running_returns_ip():
    out = _status_json("Running", ["100.64.10.20", "fd7a:115c:a1e0::1"])
    assert m.parse_tailscale_status(out) == "100.64.10.20"


def t_status_running_skips_ipv6_before_ipv4():
    out = _status_json("Running", ["fd7a:115c:a1e0::1", "100.64.10.20"])
    assert m.parse_tailscale_status(out) == "100.64.10.20"


def t_status_stopped_is_none_even_with_ip():
    # A disconnected node keeps its assigned tailnet IP.
    out = _status_json("Stopped", ["100.64.10.20"])
    assert m.parse_tailscale_status(out) is None


def t_status_needslogin_is_none():
    assert m.parse_tailscale_status(_status_json("NeedsLogin", [])) is None


def t_status_running_without_cgnat_ip_is_none():
    assert m.parse_tailscale_status(_status_json("Running", [])) is None
    assert m.parse_tailscale_status('{"BackendState": "Running"}') is None


def t_status_garbage_is_none():
    assert m.parse_tailscale_status("") is None
    assert m.parse_tailscale_status("not json") is None
    assert m.parse_tailscale_status("[1, 2]") is None


# resolve_bind_addresses: bind arg + detected ip -> ordered address list.
def t_auto_with_tailscale_ip():
    assert m.resolve_bind_addresses("auto", "100.64.10.20") == ["127.0.0.1", "100.64.10.20"]


def t_auto_without_tailscale_falls_back_to_localhost():
    assert m.resolve_bind_addresses("auto", None) == ["127.0.0.1"]


def t_explicit_localhost_ignores_tailscale():
    assert m.resolve_bind_addresses("127.0.0.1", "100.64.10.20") == ["127.0.0.1"]
    assert m.resolve_bind_addresses("localhost", "100.64.10.20") == ["127.0.0.1"]


def t_explicit_address_wins_over_auto_detection():
    assert m.resolve_bind_addresses("0.0.0.0", "100.64.10.20") == ["0.0.0.0"]
    assert m.resolve_bind_addresses("0.0.0.0", None) == ["0.0.0.0"]


# loopback_bind_failed: the loopback bind is mandatory when requested. OBS always
# reaches the relay on 127.0.0.1, so a relay that bound only the Tailscale IP
# because a stale relay holds the loopback port is a silent split-brain: 127.0.0.1
# keeps serving the old relay's pages while the new relay hides on the tailnet. A
# failed loopback bind is fatal even when other addresses bound. (#84)
def t_loopback_failed_when_requested_but_not_bound():
    # auto+Tailscale wanted [127.0.0.1, ts]; only the Tailscale IP bound.
    assert m.loopback_bind_failed(["127.0.0.1", "100.64.10.20"],
                                  ["100.64.10.20"]) is True


def t_loopback_ok_when_loopback_bound():
    assert m.loopback_bind_failed(["127.0.0.1", "100.64.10.20"],
                                  ["127.0.0.1", "100.64.10.20"]) is False
    assert m.loopback_bind_failed(["127.0.0.1"], ["127.0.0.1"]) is False


def t_loopback_not_requested_is_never_fatal():
    # Explicit --bind 0.0.0.0 or a specific IP never asked for loopback, so a
    # missing 127.0.0.1 is not this rule's concern.
    assert m.loopback_bind_failed(["0.0.0.0"], ["0.0.0.0"]) is False
    assert m.loopback_bind_failed(["100.64.10.20"], ["100.64.10.20"]) is False
    assert m.loopback_bind_failed(["0.0.0.0"], []) is False


def t_loopback_localhost_alias_counts():
    assert m.loopback_bind_failed(["localhost"], []) is True
    assert m.loopback_bind_failed(["localhost"], ["localhost"]) is False


# control_port_available: early bind probe for the mandatory loopback port.
def t_control_port_available_true_when_free_false_when_taken():
    import socket
    # A port we bind and hold -> reported unavailable.
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    try:
        assert m.control_port_available("127.0.0.1", port) is False
    finally:
        held.close()
    # Once freed, the same port is available again.
    assert m.control_port_available("127.0.0.1", port) is True


def t_control_port_probe_sets_reuseaddr_on_posix():
    # POSIX: the probe must set SO_REUSEADDR so it agrees with the authoritative
    # HTTPServer bind, which sets allow_reuse_address. Without it a port merely in
    # TIME_WAIT is falsely reported "in use" and the relay aborts a startup bind that
    # would succeed. Windows deliberately omits it, because there SO_REUSEADDR would
    # let a bind succeed against a live listener and miss a running relay.
    import socket, sys
    if sys.platform.startswith("win"):
        return
    seen = []
    real = socket.socket
    class _Rec:
        def __init__(self, *a, **k): self._s = real(*a, **k)
        def setsockopt(self, *a): seen.append(a); return self._s.setsockopt(*a)
        def bind(self, *a): return self._s.bind(*a)
        def close(self): return self._s.close()
    socket.socket = _Rec
    try:
        m.control_port_available("127.0.0.1", 0)   # ephemeral free port -> True
    finally:
        socket.socket = real
    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in seen


def t_main_probes_control_port_before_refresh_and_logs_league():
    import inspect
    src = inspect.getsource(m.main)
    assert "control_port_available(" in src
    assert src.index("control_port_available(") < src.index("pov_source")  # before the first refresh
    assert '(args.league_name or "?")' in src                    # start line uses the injected name


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

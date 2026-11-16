#!/usr/bin/env python3
"""Forwarding the relay's GT7 packets to other telemetry apps (#1130).
Run: python3 tests/test_gt7_forward.py"""
import errno, os, socket, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import gt7_crypto  # noqa: E402
import gt7_forward as fw  # noqa: E402
import gt7_telemetry  # noqa: E402
from test_gt7_telemetry import _packet  # noqa: E402


def _wire(pid=7, kind="~", **kw):
    plain = bytearray(_packet(**kw)).ljust(gt7_crypto.PACKET_LEN["~"], b"\0")
    struct.pack_into("<i", plain, 0x70, pid)
    return gt7_crypto.encrypt(bytes(plain), kind, iv=0x1234 + pid)


def t_parse_accepts_targets_and_names_every_mistake():
    targets, errors = fw.parse("127.0.0.1:20777, 192.168.1.5:33740/A ,simhub.local:5000/raw")
    assert errors == [], errors
    assert targets == [fw.Target("127.0.0.1", 20777, "raw"), fw.Target("192.168.1.5", 33740, "A"),
                       fw.Target("simhub.local", 5000, "raw")], targets
    assert fw.format_targets(targets) == "127.0.0.1:20777,192.168.1.5:33740/A,simhub.local:5000"
    assert fw.parse("") == ([], []) and fw.parse("  ") == ([], [])
    _t, errors = fw.parse("x:0,host,1.2.3.4:70000,a b:5,h:5/B,h:5,h:5")
    assert len(errors) == 6, f"each bad entry and the duplicate are reported: {errors}"
    assert any("duplicate" in e for e in errors) and any("format" in e for e in errors), errors
    _t, errors = fw.parse(",".join(f"10.0.0.{i}:5000" for i in range(fw.MAX_TARGETS + 1)))
    assert any("at most" in e for e in errors), errors


class _Sock:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail or set()

    def sendto(self, data, addr):
        if addr in self.fail:
            raise OSError(errno.ENETUNREACH, "network unreachable")  # no subclass on any OS
        self.sent.append((data, addr))


def t_forward_as_received_or_reencrypted_as_format_a():
    sock, log = _Sock(), []
    f = fw.Forwarder([fw.Target("10.0.0.2", 5000, "raw"), fw.Target("10.0.0.3", 6000, "A")],
                     sock=sock, warn=log.append)
    data = _wire(pid=42, lap=3, speed_mps=50.0)
    kind, plain = gt7_crypto.decrypt_typed(data)
    f.send(data, kind, plain)
    (raw, raw_addr), (a, a_addr) = sock.sent
    assert raw == data and raw_addr == ("10.0.0.2", 5000), "a raw target gets the packet unchanged"
    assert a_addr == ("10.0.0.3", 6000) and len(a) == gt7_crypto.PACKET_LEN["A"], len(a)
    kind_a, plain_a = gt7_crypto.decrypt_typed(a)
    pkt = gt7_telemetry.parse_packet(plain_a)
    assert kind_a == "A" and pkt.packet_id == 42 and pkt.lap == 3, (kind_a, pkt.packet_id, pkt.lap)
    assert round(pkt.speed_mps) == 50 and log == [], log


def t_a_failing_target_is_logged_once_and_never_stops_the_others():
    sock, log = _Sock(fail={("10.0.0.9", 1)}), []
    f = fw.Forwarder([fw.Target("10.0.0.9", 1, "raw"), fw.Target("10.0.0.2", 2, "raw")],
                     sock=sock, warn=log.append)
    data = _wire()
    kind, plain = gt7_crypto.decrypt_typed(data)
    for _ in range(5):
        f.send(data, kind, plain)
    assert len(sock.sent) == 5, "the healthy target gets every packet"
    assert len(log) == 1 and "10.0.0.9:1" in log[0], log
    assert f.stats() == [{"target": "10.0.0.9:1", "format": "raw", "sent": 0, "failed": 5},
                         {"target": "10.0.0.2:2", "format": "raw", "sent": 5, "failed": 0}], f.stats()
    sock.fail.clear()
    f.send(data, kind, plain)
    assert len(log) == 2 and "works again" in log[1], log


def t_a_real_udp_target_receives_and_a_closed_port_is_harmless():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2)
    closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    closed.bind(("127.0.0.1", 0))
    dead_port = closed.getsockname()[1]
    closed.close()
    f = fw.Forwarder([fw.Target("127.0.0.1", dead_port, "raw"),
                      fw.Target("127.0.0.1", rx.getsockname()[1], "A")], warn=lambda line: None)
    data = _wire(pid=5)
    kind, plain = gt7_crypto.decrypt_typed(data)
    for _ in range(3):
        f.send(data, kind, plain)
    got = [rx.recvfrom(4096)[0] for _ in range(3)]
    rx.close(); f.close()
    assert all(gt7_crypto.decrypt_typed(g)[0] == "A" for g in got), "every packet reaches the live target"


def t_names_resolve_once_at_start_and_bad_or_duplicate_ones_are_skipped():
    calls, log = [], []
    table = {"simhub.local": "10.0.0.7", "localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1"}

    def resolve(host):
        calls.append(host)
        if host not in table:
            raise OSError("name not known")
        return table[host]
    sock = _Sock()
    f = fw.Forwarder([fw.Target("simhub.local", 5000, "raw"), fw.Target("nowhere", 1, "raw"),
                      fw.Target("127.0.0.1", 9, "raw"), fw.Target("localhost", 9, "A")],
                     sock=sock, warn=log.append, resolve=resolve)
    data = _wire()
    kind, plain = gt7_crypto.decrypt_typed(data)
    for _ in range(3):
        f.send(data, kind, plain)
    assert calls == ["simhub.local", "nowhere", "127.0.0.1", "localhost"], \
        f"each name resolves once at start, never per packet: {calls}"
    assert {a for _d, a in sock.sent} == {("10.0.0.7", 5000), ("127.0.0.1", 9)}, sock.sent
    assert len(log) == 2 and "nowhere" in log[0] and "same address" in log[1], log


def t_a_windows_connection_reset_is_not_blamed_on_a_target():
    class Reset(_Sock):
        def sendto(self, data, addr):
            if not self.sent and not getattr(self, "raised", False):
                self.raised = True
                raise ConnectionResetError(10054, "reset")
            super().sendto(data, addr)
    sock, log = Reset(), []
    f = fw.Forwarder([fw.Target("10.0.0.2", 5000, "raw")], sock=sock, warn=log.append)
    data = _wire()
    kind, plain = gt7_crypto.decrypt_typed(data)
    f.send(data, kind, plain)
    f.send(data, kind, plain)
    assert log == [] and f.stats()[0]["failed"] == 0 and f.stats()[0]["sent"] == 1, (log, f.stats())


def t_a_port_in_non_ascii_digits_is_refused():
    targets, errors = fw.parse("10.0.0.1:\u00b2\u00b3")
    assert targets == [] and errors and "port" in errors[0], errors


def t_no_targets_is_a_noop():
    f = fw.Forwarder([], sock=_Sock())
    f.send(b"x", "~", b"y")
    assert f.stats() == [] and not f


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

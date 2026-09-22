#!/usr/bin/env python3
"""Stdlib unit checks for GT7 console discovery. Run: python3 tests/test_gt7_discovery.py"""
import importlib.util, os, socket

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


disc = _load("gt7_discovery", ("src", "scripts", "gt7_discovery.py"))


class _FakeSock:
    """Records sendto; yields queued (data, addr) from recvfrom, then socket.timeout."""
    def __init__(self, packets):
        self._packets = list(packets)
        self.sent = []
        self.closed = False
    def sendto(self, data, addr):
        self.sent.append((data, addr))
    def recvfrom(self, _n):
        if self._packets:
            return self._packets.pop(0)
        raise socket.timeout()
    def close(self):
        self.closed = True


def _now_seq(values):
    it = iter(values)
    def _now():
        try:
            return next(it)
        except StopIteration:
            return 10_000.0
    return _now


def _ok_decrypt(data):
    return data if data.startswith(b"OK") else None


def t_latches_only_decryptable_and_dedupes():
    packets = [
        (b"OK1", ("192.168.1.42", 33740)),   # real console
        (b"NO",  ("192.168.1.99", 33740)),   # foreign host, must be ignored
        (b"OK2", ("192.168.1.42", 33740)),   # same console again, dedup
    ]
    fake = _FakeSock(packets)
    out = disc.discover_consoles(
        timeout=2.0, sock_factory=lambda: fake, decrypt=_ok_decrypt,
        now=_now_seq([0, 0, 0, 0, 0, 100]))
    assert out["consoles"] == ["192.168.1.42"], out
    assert out["note"] == ""
    assert fake.sent and fake.sent[0][1] == (disc.BROADCAST_ADDR, disc.GT7_SEND_PORT)
    assert fake.closed is True


def t_two_consoles_sorted():
    packets = [
        (b"OK3", ("192.168.1.50", 33740)),
        (b"OK1", ("192.168.1.42", 33740)),
    ]
    out = disc.discover_consoles(
        timeout=2.0, sock_factory=lambda: _FakeSock(packets), decrypt=_ok_decrypt,
        now=_now_seq([0, 0, 0, 0, 100]))
    assert out["consoles"] == ["192.168.1.42", "192.168.1.50"], out


def t_no_reply_returns_hint():
    out = disc.discover_consoles(
        timeout=2.0, sock_factory=lambda: _FakeSock([]), decrypt=_ok_decrypt,
        now=_now_seq([0, 0, 100]))
    assert out["consoles"] == []
    assert "active session" in out["note"]


def t_socket_error_never_raises():
    def _boom():
        raise OSError("no broadcast permission")
    out = disc.discover_consoles(timeout=2.0, sock_factory=_boom, decrypt=_ok_decrypt,
                                 now=_now_seq([0, 100]))
    assert out["consoles"] == []
    assert out["note"]  # a non-empty error note


def t_decrypt_raise_and_recv_oserror_are_skipped_not_fatal():
    # recvfrom yields, in order: a packet whose decrypt raises, a non-timeout OSError
    # from recvfrom itself, then a good decodable packet. The scan must never raise,
    # must still find the good console, and must close.
    class _Sock:
        def __init__(self):
            self._script = ["raise-decrypt", "oserror",
                            (b"OKgood", ("192.168.1.42", 33740))]
            self._i = 0
            self.closed = False
        def sendto(self, _data, _addr):
            pass
        def recvfrom(self, _n):
            if self._i >= len(self._script):
                raise socket.timeout()
            item = self._script[self._i]; self._i += 1
            if item == "oserror":
                raise OSError("transient recv error")
            if item == "raise-decrypt":
                return (b"BOOM", ("192.168.1.99", 33740))
            return item
        def close(self):
            self.closed = True

    def _decrypt(data):
        if data == b"BOOM":
            raise ValueError("undecodable packet")
        return data if data.startswith(b"OK") else None

    sock = _Sock()
    out = disc.discover_consoles(
        timeout=2.0, sock_factory=lambda: sock, decrypt=_decrypt,
        now=_now_seq([0, 0, 0, 0, 0, 100]))
    assert out["consoles"] == ["192.168.1.42"], out
    assert out["note"] == ""
    assert sock.closed is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

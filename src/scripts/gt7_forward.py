"""Pass the relay's GT7 telemetry packets on to other apps on this network (#1130).

Only one program can bind UDP 33740, so SimHub or GT Race Engineer get nothing while
the relay listens. The relay sends each packet it takes from the console to every
target here, either as received (format `~`) or re-encrypted as format `A` for apps
that read only `A`. Names resolve once at start, so sending never waits for DNS and
never raises; an error the network reports is logged once per target. A closed UDP
port reports nothing on most systems, so a target that is not listening stays quiet.

Targets come from `RACECAST_GT7_FORWARD`, comma separated: `HOST:PORT` or
`HOST:PORT/A` (`/raw` is the default). Hosts are IPv4 addresses or names.
"""
import collections
import re
import socket
import struct

import gt7_crypto

ENV_NAME = "RACECAST_GT7_FORWARD"
MAX_TARGETS = 8
FORMATS = ("raw", "A")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?\Z")

Target = collections.namedtuple("Target", "host port fmt")


def parse(text):
    """(targets, errors) from the setting's text; errors name each bad entry."""
    targets, errors = [], []
    for entry in (e.strip() for e in (text or "").split(",")):
        if not entry:
            continue
        spec, _, fmt = entry.partition("/")
        fmt = fmt.strip() or "raw"
        host, sep, port = spec.strip().rpartition(":")
        if fmt not in FORMATS:
            errors.append(f"{entry}: format must be raw or A")
            continue
        if not sep or not _HOST.match(host):
            errors.append(f"{entry}: use HOST:PORT")
            continue
        if not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535:
            errors.append(f"{entry}: port must be 1-65535")
            continue
        target = Target(host, int(port), fmt)
        if any((t.host, t.port) == (host, target.port) for t in targets):
            errors.append(f"{entry}: duplicate target")
            continue
        targets.append(target)
    if len(targets) > MAX_TARGETS:
        errors.append(f"at most {MAX_TARGETS} targets")
    return targets, errors


def format_targets(targets):
    return ",".join(f"{t.host}:{t.port}" + ("/A" if t.fmt == "A" else "") for t in targets)


class Forwarder:
    """Sends each packet to every target. Falsy without targets."""

    def __init__(self, targets, sock=None, warn=None, resolve=None):
        self._warn = warn or (lambda line: None)
        resolve = resolve or _resolve
        self.targets, self._addrs = [], []
        for target in targets:
            try:
                addr = (resolve(target.host), target.port)
            except OSError as exc:
                self._warn(f"GT7 forward to {target.host}:{target.port} skipped: {exc}")
                continue
            if addr in self._addrs:
                self._warn(f"GT7 forward to {target.host}:{target.port} skipped: same address "
                           f"as an earlier target")
                continue
            self.targets.append(target)
            self._addrs.append(addr)
        self._sock = sock
        if sock is None and self.targets:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sent = [0] * len(self.targets)
        self._failed = [0] * len(self.targets)
        self._down = [False] * len(self.targets)

    def __bool__(self):
        return bool(self.targets)

    def send(self, data, kind, plain):
        """Forward one received packet. `plain` is its decryption (needed for /A)."""
        if not self.targets:
            return
        as_a = None
        for i, target in enumerate(self.targets):
            if target.fmt == "A":
                if as_a is None:
                    iv = struct.unpack_from("<I", data, 0x40)[0]
                    as_a = data if kind == "A" else gt7_crypto.encrypt(plain, "A", iv=iv)
                payload = as_a
            else:
                payload = data
            try:
                self._sock.sendto(payload, self._addrs[i])
            except ConnectionResetError:
                # Windows reports an ICMP port unreachable of an earlier send here, maybe
                # on another target's send; that one packet is lost, no target is blamed.
                continue
            except Exception as exc:  # noqa: BLE001  forwarding must never stop the telemetry loop
                self._failed[i] += 1
                if not self._down[i]:
                    self._down[i] = True
                    self._warn(f"GT7 forward to {target.host}:{target.port} failed "
                               f"(logged once until it works again): {exc}")
                continue
            self._sent[i] += 1
            if self._down[i]:
                self._down[i] = False
                self._warn(f"GT7 forward to {target.host}:{target.port} works again")

    def stats(self):
        """Per target: address, format, packets sent and failed sends."""
        return [{"target": f"{t.host}:{t.port}", "format": t.fmt, "sent": self._sent[i],
                 "failed": self._failed[i]} for i, t in enumerate(self.targets)]

    def close(self):
        if self._sock is not None:
            self._sock.close()


def _resolve(host):
    return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]

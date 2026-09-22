"""GT7 console (PlayStation) discovery over the LAN: the explicit, operator-facing
scan behind `racecast gt7-discover` and the Control Center "Discover PlayStation"
button. Reuses the GT7 heartbeat: bind 0.0.0.0:33740, broadcast a heartbeat byte to
255.255.255.255:33739, and latch a responder ONLY when its reply decrypts, which
proves it is a real GT7 console emitting valid telemetry, not any LAN host that
happens to sit on that port.

Pure-ish + best-effort: socket / clock / decrypt are injectable seams (unit-tested with
a fake socket) and the function NEVER raises. The relay (racecast-feeds.py) is
deliberately import-free, so the port constants below (GT7_RECV_PORT / GT7_SEND_PORT)
are DUPLICATED there (racecast-feeds.py also carries GT7_HEARTBEAT_S for its own loop).
Keep the shared copies in sync.
"""
import importlib.util
import os
import socket
import time

GT7_RECV_PORT = 33740          # local port we bind + the console replies to
GT7_SEND_PORT = 33739          # console's heartbeat port
GT7_HEARTBEAT = b"A"
BROADCAST_ADDR = "255.255.255.255"
NO_CONSOLE_NOTE = ("No PlayStation answered. Make sure GT7 is in an active session "
                   "(menus emit no telemetry) and the console is on this LAN.")


def _default_decrypt(data):
    """Lazily load gt7_crypto.decrypt_packet (sibling module, importlib to stay
    runnable both from src/ and the frozen bundle)."""
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "gt7_crypto", os.path.join(here, "gt7_crypto.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.decrypt_packet(data)


def _default_sock_factory():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", GT7_RECV_PORT))
    sock.settimeout(0.5)
    return sock


def discover_consoles(timeout=4.0, *, sock_factory=None, decrypt=None, now=None,
                      heartbeat_interval=1.0):
    """Scan the LAN for GT7 consoles for `timeout` seconds. Returns
    {"consoles": [ip, ...] sorted+deduped, "note": str}. Never raises."""
    sock_factory = sock_factory or _default_sock_factory
    decrypt = decrypt or _default_decrypt
    now = now or time.monotonic
    try:
        sock = sock_factory()
    except OSError as exc:
        return {"consoles": [], "note": f"discovery could not open a socket: {exc}"}
    found = set()
    try:
        start = now()
        last_hb = start - heartbeat_interval  # force an immediate first heartbeat
        while True:
            t = now()
            if t - start >= timeout:
                break
            if t - last_hb >= heartbeat_interval:
                try:
                    sock.sendto(GT7_HEARTBEAT, (BROADCAST_ADDR, GT7_SEND_PORT))
                except OSError:
                    pass  # a transient send failure: keep listening, retry next tick
                last_hb = t
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                if decrypt(data) is not None:
                    found.add(addr[0])
            except Exception:
                continue  # a malformed packet must never abort the scan
    finally:
        try:
            sock.close()
        except OSError:
            pass  # already closed or never opened; cleanup is best-effort
    consoles = sorted(found)
    return {"consoles": consoles, "note": "" if consoles else NO_CONSOLE_NOTE}

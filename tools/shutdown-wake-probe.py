#!/usr/bin/env python3
"""Does shutdown() alone wake a handler blocked in sendall? Maintainer, NOT shipped.

reap_superseded wakes the consumers OBS abandons, and which call does the waking is
per platform. No relay, no OBS: a peer that stops reading, a writer blocked in sendall,
then shutdown() and, if that was not enough, close().

On macOS shutdown() alone wakes it; on Windows it does not and close() after it does.
That split is why the relay has CLOSE_TO_WAKE. Re-run this on any host whose behaviour
is in doubt; exit 0 means something woke the writer, 1 means nothing did.
"""
import socket, sys, threading, time

srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
host, port = srv.getsockname()
peer = socket.create_connection((host, port))
peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)   # a peer that stops reading
conn, _ = srv.accept()
conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)

state = {}


def writer():
    try:
        blob = b"x" * 65536
        for _ in range(4000):                 # ~256 MB: fills every buffer and blocks
            conn.sendall(blob)
        state["result"] = "never blocked"
    except OSError as exc:
        state["result"] = f"sendall raised {type(exc).__name__}: {exc}"


t = threading.Thread(target=writer, daemon=True)
t.start()
time.sleep(3.0)
print("writer blocked in sendall:", t.is_alive())
if not t.is_alive():
    sys.exit("could not get sendall to block; the test says nothing")

conn.shutdown(socket.SHUT_RDWR)               # what reap_superseded does on its own
t.join(timeout=8)
by_shutdown = not t.is_alive()
print("woken by shutdown() alone:", by_shutdown, "->", state.get("result"))
if by_shutdown:
    sys.exit(0)

conn.close()                                  # the second half, needed on Windows
t.join(timeout=8)
print("woken by close() after it:  ", not t.is_alive(), "->", state.get("result"))
sys.exit(0 if not t.is_alive() else 1)

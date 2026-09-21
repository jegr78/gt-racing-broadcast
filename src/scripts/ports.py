#!/usr/bin/env python3
"""Cross-platform port helpers for `racecast freeport`: find and kill whatever
*listens* on a feed port so the relay can bind it again.

Only LISTENING sockets are reported: a listener is what blocks a fresh bind
(Errno 48 / WSAEADDRINUSE), and filtering on LISTEN also excludes a *client*
that merely connected TO the port (e.g. OBS pulling a feed). The kill is
deliberately PER-PROCESS (terminate the holder + reap its direct children), NOT
a session-group kill like the static-feed stop path (#133): freeport may target
a foreign process, and nuking its whole session group could take down unrelated
processes. Pure parsers + injectable command seams keep it unit-testable without
real sockets/processes (mirrors stop-streams' `looks_like_feed` style)."""
import os, re, shutil, signal, subprocess, time


FEED_PORTS = (53001, 53002, 53003)   # Feed A / Feed B / POV, the freeport default


def parse_lsof_pids(out):
    """`lsof -t` prints one PID per line. -> sorted unique ints."""
    return sorted({int(tok) for tok in out.split() if tok.strip().isdigit()})


def parse_ss_pids(out):
    """Linux `ss -ltnp` embeds the owner as `pid=<n>` in the users:(...) column."""
    return sorted({int(m) for m in re.findall(r"pid=(\d+)", out)})


def parse_fuser_pids(out):
    """`fuser <port>/tcp` prints `"<port>/tcp:  <pid> <pid>"`; PIDs follow the colon."""
    tail = out.split(":", 1)[1] if ":" in out else out
    return sorted({int(tok) for tok in tail.split() if tok.strip().isdigit()})


# A LISTENING TCP socket is the one whose FOREIGN address is the wildcard. We key
# on that, NOT the State column: netstat localizes State ("LISTENING" on English
# Windows, "ABHÖREN" on German), so matching the word makes pids_on_port find
# nothing on a non-English host and every port-recovery path silently no-op.
_NETSTAT_LISTEN_FOREIGN = ("0.0.0.0:0", "[::]:0", "*:*")


def parse_netstat_pids(out, port):
    """Windows `netstat -ano -p tcp`: columns are Proto / Local / Foreign / State /
    PID. Keep rows that LISTEN on the LOCAL address for this exact port, identified
    by the wildcard FOREIGN address, which is locale-independent. A client
    connected TO :port has a real foreign address and a localized non-listen state,
    so it is ignored; a longer port that merely shares the prefix is ignored too."""
    suffix = f":{port}"
    pids = set()
    for line in out.splitlines():
        col = line.split()
        if (len(col) >= 5 and col[0].upper() == "TCP"
                and col[2] in _NETSTAT_LISTEN_FOREIGN
                and col[1].endswith(suffix) and col[4].isdigit()):
            pids.add(int(col[4]))
    return sorted(pids)


def _run_text(argv):
    """Run a probe and return its stdout text; never raises (missing tool / non-zero
    exit -> '')."""
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              errors="replace", **_no_window()).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _no_window():
    if os.name == "nt":
        return {"creationflags": 0x08000000}   # CREATE_NO_WINDOW
    return {}


def pids_on_port(port, *, os_name=None, run=None, which=None):
    """PIDs LISTENING on `port`. POSIX prefers lsof, then ss, then fuser; Windows
    uses netstat. `run`/`which` are injectable seams (run(argv) -> stdout str)."""
    os_name = os.name if os_name is None else os_name
    run = _run_text if run is None else run
    which = shutil.which if which is None else which
    if os_name == "nt":
        return parse_netstat_pids(run(["netstat", "-ano", "-p", "tcp"]), port)
    if which("lsof"):
        # lsof present: its answer is authoritative (empty -> nothing listening).
        return parse_lsof_pids(run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]))
    if which("ss"):
        return parse_ss_pids(run(["ss", "-ltnHp", f"sport = :{port}"]))
    if which("fuser"):
        return parse_fuser_pids(run(["fuser", f"{port}/tcp"]))
    return []


def decide_free(pids, owned, force):
    """Pure gate. ('clear', []) when nothing listens; ('refuse', pids) when a
    RUNNING racecast service legitimately owns the port and --force was not given
    (freeing it would cut the live broadcast); ('free', pids) otherwise."""
    if not pids:
        return ("clear", [])
    if owned and not force:
        return ("refuse", pids)
    return ("free", pids)


def _proc_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_pid(pid, *, os_name=None, call=None, kill=None, sleep=None, alive=None):
    """Terminate the single process holding a port (and reap its direct children),
    escalating SIGTERM -> SIGKILL. Per-process by design; see the module docstring.
    Seams (`call`/`kill`/`sleep`/`alive`) make it testable without real processes."""
    os_name = os.name if os_name is None else os_name
    call = subprocess.call if call is None else call
    kill = os.kill if kill is None else kill
    sleep = time.sleep if sleep is None else sleep
    alive = _proc_alive if alive is None else alive
    if os_name == "nt":
        call(["taskkill", "/PID", str(pid), "/T", "/F"],
             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    call(["pkill", "-P", str(pid)], stderr=subprocess.DEVNULL)   # children first, best-effort
    try:
        kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return  # already gone
    for _ in range(25):                  # ~2.5 s grace before escalating
        if not alive(pid):
            break
        sleep(0.1)
    if alive(pid):
        try:
            kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # exited between the poll and the kill, or not ours to signal

#!/usr/bin/env python3
"""Stop every streamlink server started by start-streams.py, on any platform."""
import argparse, glob, os, signal, subprocess, time


def looks_like_feed(probe_output, windows=False):
    """True iff a ps or tasklist probe line describes one of our feed processes: a
    loopstream or streamlink child, its python.exe image on Windows, or the frozen
    racecast binary running the hidden `streams run-feed` verb. This guards against
    a stale or recycled PID file naming an unrelated process.

    On POSIX the full command line is available, so it requires either "run-feed"
    or a loopstream or streamlink token. A bare "python" is NOT accepted, because
    any python process would match. On Windows the probe returns only the image
    name, so "python", "streamlink" and "racecast.exe" are accepted."""
    text = probe_output.lower()
    if "run-feed" in text or "racecast.exe" in text:   # frozen children (both platforms)
        return True
    tokens = ("python", "streamlink") if windows else ("loopstream", "streamlink")
    return any(tok in text for tok in tokens)


def pid_is_feed(pid):
    """True only if `pid` is actually one of our feed processes. Without this guard
    a stale or forged PID file naming a recycled PID could get an unrelated process
    SIGTERMed. On POSIX it matches the command line against our launchers."""
    if os.name == "nt":
        try:
            # errors="replace": tasklist writes OEM-codepage console output that
            # the ANSI codepage cannot decode; the matched tokens are pure ASCII.
            out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                                          stderr=subprocess.DEVNULL, text=True,
                                          errors="replace")
        except (subprocess.SubprocessError, OSError):
            return False
        return looks_like_feed(out, windows=True)
    try:
        cmd = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="],
                                      stderr=subprocess.DEVNULL, text=True,
                                      errors="replace")
    except (subprocess.SubprocessError, OSError):
        return False
    return looks_like_feed(cmd)


def _proc_alive(pid):
    """True if `pid` still exists on POSIX. Signal 0 is an existence probe and
    sends nothing. PermissionError means the PID is live but not ours, so alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_tree(pid):
    """Terminate a feed process AND every descendant.

    Static feeds are spawned as session leaders (services.spawn_kwargs ->
    start_new_session=True), so the whole tree shares ONE process group whose id is
    `pid`. Signalling the GROUP reaps grandchildren too, which the frozen binary
    needs: its tree is bootloader(pid) -> app-child -> streamlink, and a
    direct-children-only `pkill -P pid` orphaned streamlink, which kept its port
    bound and blocked the relay's Feed A (#133). Best-effort throughout;
    pid_is_feed() vetted `pid` before we get here."""
    if os.name == "nt":
        # taskkill /T already walks and kills the whole tree.
        subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return  # already gone
    if pgid != pid:
        # Not the session leader a feed always is, so fall back to the narrower
        # kill rather than risk signalling an unrelated process group.
        subprocess.call(["pkill", "-P", str(pid)], stderr=subprocess.DEVNULL)
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass  # leader already gone or not ours, nothing left to signal
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    # Escalate to SIGKILL on the group if the leader is still up after a grace
    # period; a wedged streamlink that ignores SIGTERM would else hold its port.
    for _ in range(25):                  # ~2.5 s grace
        if not _proc_alive(pid):
            break
        time.sleep(0.1)
    if _proc_alive(pid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # group already reaped between the grace poll and the kill


def state_dir(here):
    """Must match start-streams.py: <repo>/runtime/static from the repo, next to
    the script from the distributed package."""
    if os.path.basename(here) == "scripts" and os.path.basename(os.path.dirname(here)) == "src":
        return os.path.join(os.path.dirname(os.path.dirname(here)), "runtime", "static")
    return here


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=state_dir(here))
    a = ap.parse_args()
    pidfiles = glob.glob(os.path.join(a.state_dir, "feed_*.pid"))
    for pf in pidfiles:
        try:
            with open(pf) as fh:
                pid = int(fh.read().strip())
        except ValueError:
            os.remove(pf); continue
        if not pid_is_feed(pid):
            print(f"Skipped {os.path.basename(pf)[:-4]} (PID {pid} is not a feed, stale file)")
            os.remove(pf); continue
        kill_tree(pid)
        print(f"Stopped {os.path.basename(pf)[:-4]} (PID {pid})")
        os.remove(pf)
    # No broad `pkill -f player-external-http` here: the relay serves with the same
    # flag, so a catch-all would kill live relay feeds. Only the tracked PID files
    # are stopped.
    if not pidfiles:
        print("No running feeds found.")
    print("Done.")


if __name__ == "__main__":
    main()

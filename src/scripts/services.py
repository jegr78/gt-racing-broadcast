"""Manage spawned background services (relay, streams) via a PID file + log file.
Pure decision logic (read_pid, pid_alive, status_line) is separated from process
side effects (start_detached, stop_pid, tail) so it unit-tests without spawning."""
import os, signal, subprocess, sys, time


def read_pid(pid_path):
    """Int PID stored in pid_path, or None if missing/empty/garbage."""
    try:
        with open(pid_path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def spawn_kwargs(os_name):
    """Popen kwargs that launch a background daemon detached from our console per OS.

    Windows: CREATE_NO_WINDOW, NOT DETACHED_PROCESS. The frozen onefile relay is a
    two-process tree: the PyInstaller bootloader spawns the real app as a child.
    Under DETACHED_PROCESS the bootloader has NO console, so when it starts the
    inner app Windows allocates a FRESH, VISIBLE console for it that stays open for
    the entire event (the relay never exits). CREATE_NO_WINDOW instead gives the
    bootloader a HIDDEN console that the inner process inherits, so no window. The
    daemon still outlives us (Windows processes are independent) and
    CREATE_NEW_PROCESS_GROUP keeps it from catching the parent terminal's Ctrl+C.
    Mirrors no_window_kwargs' flag; kept separate because daemons also need the
    process-group isolation that one-shot probes do not."""
    if os_name == "posix":
        return {"start_new_session": True}
    if os_name == "nt":
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {}


def no_window_kwargs(os_name=None):
    """Popen/run kwargs that stop a console child from flashing its own terminal
    window on Windows. A frozen --windowed app (racecast-ui.exe) has NO console, so
    every console subprocess it spawns, such as tasklist, the tailscale CLI or the
    sibling racecast.exe, otherwise pops a transient terminal window, and the Control
    Center's 2-3 s status poll did it continuously (#23). CREATE_NO_WINDOW gives the
    child a hidden console instead; children of such a process inherit that hidden
    console, so applying it at the job root suppresses the whole tree. Harmless
    when a console already exists, and a no-op (empty kwargs) off Windows so the
    same call site stays cross-platform."""
    os_name = os.name if os_name is None else os_name
    if os_name == "nt":
        CREATE_NO_WINDOW = 0x08000000
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


# The single source of truth: every spawn site imports this (relay scripts add
# src/scripts to sys.path; scripts/ siblings import it directly).
def external_tool_env(frozen=None, environ=None):
    """Environment for spawning an EXTERNAL native tool (yt-dlp, streamlink,
    ffmpeg, deno, the tailscale CLI) from a possibly PyInstaller-frozen process.

    The onefile bootloader prepends its private _MEIPASS extraction dir to
    LD_LIBRARY_PATH (DYLD_LIBRARY_PATH on macOS) so the BUNDLED interpreter finds
    its own shared libs. An external tool that links the SYSTEM libraries, such as
    yt-dlp or streamlink running under the system Python, whose _ssl needs the system
    libcrypto, then mis-loads our older bundled libcrypto and dies with
    "version `OPENSSL_x.y.z' not found". Strip every PyInstaller extraction dir from
    the path, this process's _MEIPASS AND any parent's: a frozen Control Center that
    re-invokes the frozen binary leaves the PARENT's _MEIPASS on the child's
    LD_LIBRARY_PATH, and the bootloader's <VAR>_ORIG points at THAT, so merely
    restoring _ORIG reintroduces a bundled libcrypto. Keep any genuinely external
    entries; drop the var when nothing remains. Returns None when not frozen, so the
    caller inherits os.environ unchanged and dev/source runs stay untouched."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return None
    env = dict(os.environ if environ is None else environ)
    meipass = getattr(sys, "_MEIPASS", "")
    meipass = os.path.normpath(meipass) if meipass else ""
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if var not in env:
            continue
        kept = []
        for part in env[var].split(os.pathsep):
            norm = os.path.normpath(part) if part else ""
            if not part or os.path.basename(norm).startswith("_MEI"):
                continue
            if meipass and norm == meipass:
                continue
            kept.append(part)
        if kept:
            env[var] = os.pathsep.join(kept)
        else:
            env.pop(var, None)
    return env


def daemon_bundle_env(env, bundle_dir, os_name=None):
    """Copy of *env* with the temp dir pointed at *bundle_dir*.

    A frozen onefile daemon unpacks src/ into the OS temp dir and reads from it
    for its whole life, but every OS reaps that dir on a schedule without
    checking whether anything is still using it (macOS after 3 days, Linux
    after 10). A relay left running between two events therefore loses its HUD
    pages while looking perfectly healthy.

    The bootloader honours TMPDIR/TMP/TEMP and reads them BEFORE Python starts,
    which is why this can only be done for a child we spawn, never for ourselves
    after the fact. It is free here: the child is starting anyway and unpacks
    exactly once either way.

    An empty bundle_dir returns the environment unchanged, so a source run keeps
    the OS default.
    """
    if not bundle_dir:
        return env
    out = dict(env)
    for var in (("TMP", "TEMP") if (os_name or os.name) == "nt" else ("TMPDIR",)):
        out[var] = bundle_dir
    return out


def stop_commands(os_name, pid, force):
    """argv to stop a PID on Windows (taskkill), or None where POSIX signals apply.
    /T kills the child tree, so the relay's streamlink/yt-dlp children are not
    orphaned. The non-force form asks first (WM_CLOSE); console children usually
    ignore it, so stop_pid() falls through to the force form after the timeout."""
    if os_name != "nt":
        return None
    if force:
        return ["taskkill", "/F", "/T", "/PID", str(pid)]
    return ["taskkill", "/PID", str(pid)]


def pid_alive(pid):
    """True iff a process with this PID currently exists."""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid):
    """ctypes probe: os.kill(pid, 0) is NOT safe on Windows, where any signal other
    than CTRL_C/CTRL_BREAK unconditionally TerminateProcess()es the target."""
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED  # exists, no access
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def status_line(name, pid, alive, extra=""):
    """One formatted line: 'relay          RUNNING (pid 1234)  <extra>'.
    Pad width 14 keeps the longest name (e.g. 'streams:53002') aligned."""
    state = f"RUNNING (pid {pid})" if alive else "stopped"
    return f"{name:<14} {state}  {extra}".rstrip()


def start_detached(argv, log_path, pid_path, env=None):
    """Spawn argv detached, stdout/stderr -> log_path, write pid_path. Returns PID.
    Caller must verify it is not already running first.
    env: optional full environment dict for the child (defaults to inherited)."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(os.path.dirname(pid_path), exist_ok=True)
    kwargs = spawn_kwargs(os.name)
    # Open the log in a `with` so the parent's fd is closed after Popen dups it
    # into the child, avoiding a parent-side fd leak that would also pin the file.
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, **kwargs)
    with open(pid_path, "w") as fh:
        fh.write(str(proc.pid))
    return proc.pid


def _reap_zombie(pid):
    """Attempt waitpid(WNOHANG) to reap a zombie child; silently ignore if not our child."""
    if os.name != "posix":
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass  # not our child, nothing to reap
    except OSError:
        pass  # already reaped or gone


def looks_like_relay(probe_output, windows=False):
    """True iff a ps/tasklist probe line describes our relay daemon: the frozen
    binary running `relay run`, or python running racecast-feeds.py. Guards a
    stale/recycled PID file from making stop_pid signal an unrelated process,
    mirroring stop-streams.py's looks_like_feed. On Windows tasklist returns only
    the image name (no argv), so we accept the binary and the python host."""
    text = probe_output.lower()
    if "racecast-feeds" in text:              # repo mode: python racecast-feeds.py
        return True
    if "relay" in text and "run" in text:     # frozen: racecast relay run
        return True
    if windows:
        return "racecast.exe" in text or "python" in text
    return False


def _pid_cmdline(pid):
    """Lowercase-able process text for `pid`: the full command line on POSIX, the
    tasklist CSV (image name only) on Windows. '' when the probe fails / PID gone."""
    if os.name == "nt":
        try:
            return subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                stderr=subprocess.DEVNULL, text=True, errors="replace",
                **no_window_kwargs())
        except (subprocess.SubprocessError, OSError):
            return ""
    try:
        return subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            stderr=subprocess.DEVNULL, text=True, errors="replace")
    except (subprocess.SubprocessError, OSError):
        return ""


def pid_is_relay(pid):
    """True only if `pid` is actually our relay daemon. Guards stop_pid against a
    stale/recycled PID file the same way stop-streams.py's pid_is_feed does."""
    return looks_like_relay(_pid_cmdline(pid), windows=(os.name == "nt"))


def _signal_stop(pid, force):
    cmd = stop_commands(os.name, pid, force)
    if cmd is not None:
        subprocess.run(cmd, capture_output=True, **no_window_kwargs(os.name))
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        pass  # process already exited between the alive-check and the kill


def stop_pid(pid, pid_path=None, timeout=10, is_target=None):
    """Graceful stop, wait up to timeout, then force; remove pid_path. True if gone.

    `is_target(pid) -> bool`, when given, verifies the live PID is really our
    process before signalling it: a stale/recycled PID file naming an unrelated
    process is dropped (pid file removed, reported gone) without ever killing the
    impostor. Without it, behaviour is unchanged."""
    if pid_alive(pid):
        if is_target is not None and not is_target(pid):
            # Recycled or foreign PID: our daemon is already gone, never kill it.
            if pid_path and os.path.exists(pid_path):
                os.remove(pid_path)
            return True
        _signal_stop(pid, force=False)
        for _ in range(timeout * 2):
            _reap_zombie(pid)
            if not pid_alive(pid):
                break
            time.sleep(0.5)
        if pid_alive(pid):
            _signal_stop(pid, force=True)
            time.sleep(0.5)
            _reap_zombie(pid)
    if pid_path and os.path.exists(pid_path):
        os.remove(pid_path)
    return not pid_alive(pid)


def tail(log_path, follow=False, lines=40):
    """Print the last `lines` of log_path; if follow, stream new output until Ctrl+C.
    Pure Python and cross-platform, with no system `tail`."""
    if not os.path.exists(log_path):
        print(f"(no log yet at {log_path})")
        return
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        for line in fh.readlines()[-lines:]:
            sys.stdout.write(line)
        if not follow:
            return
        try:
            while True:
                line = fh.readline()
                if line:
                    sys.stdout.write(line); sys.stdout.flush()
                else:
                    time.sleep(0.3)
        except KeyboardInterrupt:
            pass  # Ctrl+C ends an interactive tail cleanly


def tail_merged(paths, follow=False, lines=40, label_of=None):
    """Tail several files into one stream, each line prefixed with its source
    (`[basename] line`). Non-follow: print the last `lines` of each file, source
    order. Follow: poll all files and emit new lines as they arrive (arrival order).
    `label_of(path) -> str` overrides the source label (default: basename w/o .log)."""
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        print("(no log yet)")
        return
    def lbl(p):
        return label_of(p) if label_of else os.path.basename(p).split(".log")[0]
    # Re-open per read inside a `with` block, tracking a byte offset per file.
    # CodeQL's py/file-not-closed cannot trace a close through a handle list OR
    # through contextlib.ExitStack (#217), so a `with open()` is the only pattern it
    # accepts as definitely-closed.
    pos = {}
    for p in paths:
        with open(p, "rb") as fh:
            data = fh.read()
            pos[p] = fh.tell()
        for line in data.decode("utf-8", "replace").splitlines()[-lines:]:
            sys.stdout.write(f"[{lbl(p)}] {line}\n")
    if not follow:
        return
    try:
        while True:
            quiet = True
            for p in paths:
                with open(p, "rb") as fh:
                    fh.seek(pos[p])
                    chunk = fh.read()
                    pos[p] = fh.tell()
                if chunk:
                    for line in chunk.decode("utf-8", "replace").splitlines():
                        sys.stdout.write(f"[{lbl(p)}] {line}\n")
                    sys.stdout.flush()
                    quiet = False
            if quiet:
                time.sleep(0.3)
    except KeyboardInterrupt:
        pass  # Ctrl+C ends an interactive tail cleanly

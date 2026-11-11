"""Keep the producer PC from idle sleep while the relay runs (#1105).

Stdlib only: the standalone relay imports it. Only idle sleep is blocked; display
sleep, a closed lid and a sleep the operator chooses stay untouched. Every block
ends with the relay process, so a crash or kill leaves none behind.
"""
import logging
import shutil
import subprocess
import sys
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
_FALSEY = {"0", "false", "no", "off"}


def enabled(environ):
    """False only for an explicit falsey RACECAST_KEEP_AWAKE in the machine .env."""
    return str(environ.get("RACECAST_KEEP_AWAKE", "")).strip().lower() not in _FALSEY


def command(platform, pid, which=shutil.which):
    """(argv, note) for the helper process that blocks idle sleep until `pid` exits.
    argv is None on Windows (no helper) and when a helper is missing."""
    if platform == "darwin":
        caffeinate = which("caffeinate")
        if not caffeinate:
            return None, "caffeinate not found"
        return [caffeinate, "-i", "-w", str(pid)], ""
    if platform.startswith("linux"):
        inhibit, tail = which("systemd-inhibit"), which("tail")
        if not inhibit:
            return None, "systemd-inhibit not found"
        if not tail:
            return None, "tail not found"
        return [inhibit, "--what=idle:sleep", "--mode=block", "--who=racecast",
                "--why=relay running", tail, f"--pid={pid}", "-f", "/dev/null"], ""
    return None, f"unsupported platform {platform}"


def _kernel32():
    import ctypes
    k = ctypes.WinDLL("kernel32")
    k.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    k.SetThreadExecutionState.restype = ctypes.c_uint32
    return k


class KeepAwake:
    """Holds the block for this process; start() and stop() never raise."""

    def __init__(self, pid, *, platform=sys.platform, which=shutil.which,
                 popen=subprocess.Popen, kernel32=None, environ=None, log=None):
        self.pid = pid
        self.platform = platform
        self.which = which
        self.popen = popen
        self.kernel32 = kernel32
        self.environ = environ if environ is not None else {}
        self.log = log or logging.getLogger("racecast")
        self.method = None
        self.note = "not started"
        self._held = False
        self._proc = None
        self._stop = threading.Event()
        self._thread = None
        self._watch = None

    def start(self):
        if not enabled(self.environ):
            self.note = "off (RACECAST_KEEP_AWAKE=0)"
            self.log.info("keep-awake: %s", self.note)
            return
        try:
            if self.platform == "win32":
                self._start_windows()
            else:
                self._start_helper()
        except Exception as exc:  # noqa: BLE001  a failed block never stops the relay
            self._fail(str(exc))

    def _fail(self, note):
        self._held = False
        self.note = note
        self.log.warning("keep-awake: idle sleep NOT blocked (%s)", note)

    def _start_windows(self):
        self.method = "SetThreadExecutionState"
        kernel = self.kernel32 or _kernel32()
        ready = threading.Event()

        def hold():
            if not kernel.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                self.note = "SetThreadExecutionState refused"
                ready.set()
                return
            self._held = True
            self.note = ""
            ready.set()
            self._stop.wait()
            kernel.SetThreadExecutionState(ES_CONTINUOUS)
            self._held = False

        self._thread = threading.Thread(target=hold, name="keep-awake", daemon=True)
        self._thread.start()
        ready.wait(5)
        if self._held:
            self.log.info("keep-awake: idle sleep blocked (%s)", self.method)
        else:
            self._fail(self.note if self.note != "not started" else "no answer from Windows")

    def _start_helper(self):
        argv, note = command(self.platform, self.pid, self.which)
        if argv is None:
            self.note = note
            self.log.warning("keep-awake: idle sleep NOT blocked (%s)", note)
            return
        self.method = argv[0].replace("\\", "/").rsplit("/", 1)[-1]
        self._proc = self.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, errors="replace")
        self._held = True
        self.note = ""
        self.log.info("keep-awake: idle sleep blocked (%s)", self.method)
        self._watch = threading.Thread(target=self._watch_helper, args=(self._proc,),
                                       name="keep-awake", daemon=True)
        self._watch.start()

    def _watch_helper(self, proc):
        try:
            _, err = proc.communicate()
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        if self._stop.is_set():
            return
        lines = [x.strip() for x in (err or "").splitlines() if x.strip()]
        self._fail(lines[-1] if lines else f"{self.method} exited ({proc.poll()})")

    def wait_watch(self, timeout):
        """Test seam: wait until the helper watcher has finished."""
        if self._watch is not None:
            self._watch.join(timeout)

    def status(self):
        held = self._held
        if self._proc is not None and self._proc.poll() is not None:
            held = False
        return {"held": held, "method": self.method, "note": self.note}

    def stop(self):
        self._stop.set()
        try:
            if self._thread is not None:
                self._thread.join(2)
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        self._held = False

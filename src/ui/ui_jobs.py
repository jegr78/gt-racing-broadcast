"""Control Center job manager: run one `racecast <args>` child per triggered
operation, keep its combined stdout/stderr lines in memory for the web UI
(poll or SSE), and refuse a second concurrent run of the same operation.
Jobs are subprocesses, not threads, because sys.stdout is process-global, so
parallel in-process ops would interleave output, and a child can be killed.
Spec: docs/superpowers/specs/2026-06-07-control-center-design.md."""
import os, subprocess, tempfile, threading, uuid

# A frozen --windowed app (racecast-ui.exe) has no console, so spawning the console
# sibling racecast.exe pops a terminal window per job (#23). CREATE_NO_WINDOW gives
# the child a hidden console; its own subprocess children inherit it, so this one
# flag at the job root suppresses the whole tree. The constant is inlined because
# ui_jobs is import-isolated from scripts/ in its test.
_NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}
MACHINE_OPS = frozenset({"gt7-data-update", "shift-reference-update", "obs-benchmark",
                         "console-setup-funnel", "console-apply-funnel", "smoketest",
                         "companion-enable-control", "obs-browser"})


class Job:
    def __init__(self, job_id, op, proc, profile=None):
        self.id, self.op, self.proc = job_id, op, proc
        self.profile = profile
        self.lines = []          # decoded output lines (head-trimmed, see dropped)
        self.dropped = 0         # lines trimmed off the head; keeps indices stable
        self.exit_code = None
        self.cancelled = False   # cancel() was requested (exit code will be non-zero)
        self.lock = threading.Lock()


class JobManager:
    def __init__(self, argv_for, env=None, spawn=None, max_lines=5000, logger=None):
        """argv_for(op_args) -> child argv (see ui_ops.job_argv). env: full
        child environment or None (inherit). spawn: Popen-compatible test seam.
        logger: optional logging.Logger. When set, the action's start marker, each
        output line (prefixed `[op]`) and its exit code are written to the log file;
        the in-memory buffer and SSE are unchanged. None means silent."""
        self.argv_for, self.env = argv_for, env
        self.spawn = spawn or self._spawn
        self.max_lines = max_lines
        self.logger = logger
        self.jobs = {}           # job_id -> Job, kept for the session; the op set is finite
        self.lock = threading.Lock()
        self.stopping = False

    def prepare_shutdown(self):
        """Refuse shutdown during smoke cleanup and atomically close admission."""
        with self.lock:
            if any(job.op == "smoketest" and job.exit_code is None for job in self.jobs.values()):
                return False
            self.stopping = True
            return True

    def _spawn(self, argv, input_text=None):
        if input_text is not None:
            # A finite, already typed confirmation, never an interactive stdin
            # bridge or automatic yes. A file avoids blocking on a child pipe.
            with tempfile.TemporaryFile() as confirmed:
                confirmed.write(input_text.encode('utf-8'));confirmed.seek(0)
                return subprocess.Popen(argv, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=confirmed, env=self.env, **_NO_WINDOW)
        return subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=self.env,
                                **_NO_WINDOW)

    def start(self, op, op_args, input_text=None):
        """Start `op` unless one is still running. Returns (job_id, None), or the
        running job's id and an error text."""
        if input_text is not None and (op != 'smoketest' or not isinstance(input_text,str) or len(input_text)>512):
            raise ValueError('Only a finite typed smoke confirmation is accepted')
        with self.lock:
            if self.stopping:
                return None, "UI service is stopping"
            profile = op_args[1] if op_args[:1] == ["--profile"] else None
            for job in self.jobs.values():
                if job.op == op and (job.profile == profile or op in MACHINE_OPS) and job.exit_code is None:  # exit_code writes are atomic
                    return job.id, f"{op} is already running"
            argv = self.argv_for(op_args)
            proc = self.spawn(argv) if input_text is None else self.spawn(argv, input_text=input_text)
            job = Job(uuid.uuid4().hex[:12], op, proc, profile)
            self.jobs[job.id] = job
        if self.logger:
            self.logger.info("[%s] action started, argv: %s", op, " ".join(argv))
        reader = threading.Thread(target=self._reader, args=(job,), daemon=op != "smoketest")
        try:
            reader.start()
        except RuntimeError as exc:      # OS thread exhaustion; unblock the op
            with job.lock:
                job.exit_code = -1
                job.lines.append(f"(could not start output reader: {exc})")
            if self.logger:
                self.logger.warning("[%s] action finished, exit -1 (%s)", op, exc)
        return job.id, None

    def _reader(self, job):
        for raw in job.proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if self.logger:
                self.logger.info("[%s] %s", job.op, line)
            with job.lock:
                job.lines.append(line)
                overflow = len(job.lines) - self.max_lines
                if overflow > 0:
                    del job.lines[:overflow]
                    job.dropped += overflow
        job.proc.stdout.close()          # release the pipe fd promptly
        code = job.proc.wait()
        with job.lock:
            job.exit_code = code
        if self.logger:
            log = self.logger.info if code == 0 else self.logger.warning
            log("[%s] action finished, exit %s", job.op, code)

    def snapshot(self, job_id):
        """{'id','op','running','exit_code','cancelled'} or None for an unknown id."""
        job = self.jobs.get(job_id)  # GIL-atomic dict read; no self.lock needed
        if job is None:
            return None
        with job.lock:
            return {"id": job.id, "op": job.op, "profile": job.profile,
                    "running": job.exit_code is None, "exit_code": job.exit_code,
                    "cancelled": job.cancelled}

    def cancel(self, job_id):
        """Request termination of a running job. True = signalled, False =
        already finished, None = unknown id. Terminates only the direct child, so
        a daemon the child already detached keeps running: cancel means 'stop this
        action', not 'tear down services'."""
        job = self.jobs.get(job_id)        # GIL-atomic dict read
        if job is None:
            return None
        with job.lock:
            # TerminateProcess/SIGTERM bypass the smoke CLI's event teardown.
            if job.exit_code is not None or job.op == "smoketest":
                return False
            job.cancelled = True
        try:
            job.proc.terminate()
        except OSError:
            pass                           # exited between the check and the signal
        return True

    def lines_since(self, job_id, since):
        """(new lines from absolute index `since`, next index, exit_code).
        (None, since, None) for an unknown id. Head-trimmed lines are skipped and
        `since` stays an absolute position, so SSE/poll clients never re-read."""
        job = self.jobs.get(job_id)  # GIL-atomic dict read; no self.lock needed
        if job is None:
            return None, since, None
        with job.lock:
            start = max(since - job.dropped, 0)
            chunk = list(job.lines[start:])
            return chunk, job.dropped + len(job.lines), job.exit_code

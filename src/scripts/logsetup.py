"""Reusable, mostly-pure logging helpers for the racecast daemons and log surface.
stdlib-only by design — DO NOT import config.py here (the relay stays
dependency-light, like the other self-contained scripts). Covers: rotating
timestamped loggers, the single log-prune authority, subprocess line
classification + a pump, external-app log discovery, and archive resolution."""
import logging, os, re, sys, time
from logging.handlers import TimedRotatingFileHandler

DEFAULT_RETENTION_DAYS = 7


def harden_stdio(streams=None, environ=None):
    """Make this process, and every child it spawns, survive a narrow console.

    The in-process half is issue #24's `_force_utf8_io`, moved here verbatim in
    behaviour so the relay and the helper scripts can reach it too — it used to live in
    racecast.py, which the dependency-light scripts must not import, so only the CLI was
    protected. That is why the relay crashed at startup on the German producer host
    2026-09-21: its argparse help carries an arrow, the console was cp1252, and nothing
    had reconfigured stdout.

    UTF-8 rather than the console's own encoding, deliberately (#24): whenever stdout is
    a PIPE — every Control Center job — Python picks the locale encoding, and those
    captured bytes are rendered in a UTF-8 web UI. `errors="replace"` is the backstop so
    an un-encodable character degrades to "?" instead of raising.

    The child half is new and is what makes this stop recurring. Setting
    `PYTHONIOENCODING` in our environment reaches every child whatever spawn site
    creates it — 17 entrypoints under src/ and 124 LOG/print lines carrying non-ASCII,
    none of which have to be edited or kept in sync. An operator who set it deliberately
    keeps their value.

    Best-effort throughout: it runs before anything else in main(), so it must not be
    able to break a start. A stream that is None (a --windowed build has no stdout),
    predates reconfigure(), or rejects it is silently skipped."""
    env = os.environ if environ is None else environ
    for stream in (streams if streams is not None else (sys.stdout, sys.stderr)):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass    # stream missing/old/non-reconfigurable — leave it as-is
    try:
        env.setdefault("PYTHONIOENCODING", "utf-8:replace")
    except Exception:          # noqa: BLE001 — never fatal
        pass


class _ResilientTimedRotatingFileHandler(TimedRotatingFileHandler):
    """A TimedRotatingFileHandler whose midnight rollover never drops a log
    record. On Windows the rollover rename fails (PermissionError / WinError 32)
    while another process holds the file open — the Control Center tails
    relay.console.log and feed_*.log live, and Windows forbids renaming an open
    file. The stock handler lets that exception escape emit(), so every line is
    lost to stderr and NOTHING reaches the timestamped log. Here a failed
    rollover re-opens the still-present base file (so the in-flight record is
    written) and pushes the next attempt forward one interval (so we don't
    re-attempt — and re-fail — on every subsequent emit). POSIX is unaffected: it
    can rename an open file, so the rollover succeeds and this path never runs."""
    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                self.stream = self._open()
            now = int(time.time())
            while self.rolloverAt <= now:
                self.rolloverAt += self.interval


def read_new_lines(path, pos):
    """Read whole lines appended to `path` since byte offset `pos`, returning
    (lines, new_pos). RE-OPENS AND CLOSES the file each call so a concurrent
    writer can rotate/rename it on Windows — a continuously-held read handle is
    exactly what blocks the relay's midnight rollover. A half-written trailing
    line (no terminating newline yet) is held back for the next poll. On
    rotation/truncation (file now shorter than `pos`) it restarts from offset 0.
    A missing/unreadable file yields ([], pos) so the caller just retries."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() < pos:
                pos = 0                  # rotated or truncated — re-read from top
            fh.seek(pos)
            data = fh.read()
    except OSError:
        return [], pos                   # vanished mid-rotation — retry next poll
    nl = data.rfind(b"\n")
    if nl == -1:
        return [], pos                   # no complete line yet
    consumed = data[:nl + 1]
    return consumed.decode("utf-8", "replace").splitlines(), pos + len(consumed)


def configure_logging(name, log_path, level=logging.INFO, to_stdout=None):
    """A logging.Logger writing timestamped, leveled lines to log_path with daily
    midnight rotation (archive suffix `.YYYY-MM-DD`). backupCount=0 -> the handler
    never deletes; prune_old_logs is the sole deletion authority. A stdout
    StreamHandler is added only on a TTY (foreground run) — a daemon whose stdout is
    a redirected file must not double-write. Idempotent per `name`."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if any(getattr(h, "_racecast", False) for h in logger.handlers):
        return logger
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = _ResilientTimedRotatingFileHandler(log_path, when="midnight",
                                            backupCount=0, encoding="utf-8",
                                            delay=True)
    fh.setFormatter(fmt)
    fh._racecast = True
    logger.addHandler(fh)
    on_tty = sys.stdout.isatty() if to_stdout is None else to_stdout
    if on_tty:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh._racecast = True
        logger.addHandler(sh)
    return logger


def close_logging(name):
    """Close and detach the rotating handler(s) this module attached to `name`.
    Tests that point a logger at a TemporaryDirectory MUST call this before the dir
    is removed: Windows refuses to delete a file with an open handle (POSIX allows
    it, so it only bites the Windows CI runner). No-op if the logger has none."""
    logger = logging.getLogger(name)
    for h in list(logger.handlers):
        if getattr(h, "_racecast", False):
            h.close()
            logger.removeHandler(h)


def prune_old_logs(log_dir, keep_days=DEFAULT_RETENTION_DAYS, now_ts=None):
    """Delete files in log_dir whose mtime is older than keep_days; return the
    removed paths. The ONLY log-deletion path — covers every log type (rotated
    console/feed logs, *.boot.log, the Tailscale snapshot, any local OBS copies).
    `now_ts` is injectable for deterministic tests. Best-effort: unreadable dir or
    a vanishing file is skipped, never raised."""
    now_ts = time.time() if now_ts is None else now_ts
    cutoff = now_ts - keep_days * 86400
    removed = []
    try:
        names = os.listdir(log_dir)
    except OSError:
        return removed
    for name in names:
        p = os.path.join(log_dir, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                removed.append(p)
        except OSError:
            pass  # file vanished between listdir and remove — skip it
    return sorted(removed)


_ERROR_HINTS = ("error", "fatal", "forbidden", "403", "401", "traceback",
                "exception", "failed", "denied", "could not", "no such")
_WARN_HINTS = ("warn", "retry", "retrying", "unable", "timeout", "timed out",
               "waiting for")


def classify_subproc_line(line):
    """Heuristic logging level for one pumped subprocess line."""
    low = line.lower()
    if any(h in low for h in _ERROR_HINTS):
        return logging.ERROR
    if any(h in low for h in _WARN_HINTS):
        return logging.WARNING
    return logging.INFO


URL_SHORTEN_MAX = 120
_URL_RE = re.compile(r"https?://[^\s]+")
_ITAG_RE = re.compile(r"[/=]itag[/=](\d+)")
_DIGITS_RE = re.compile(r"\d+")


def shorten_urls(text, max_len=URL_SHORTEN_MAX):
    """Replace each URL longer than max_len with a compact host-only form, dropping
    the path+query (where googlevideo sig/lsig tokens live) and keeping the itag for
    diagnostics. URLs <= max_len and non-URL text are returned unchanged. Pure."""
    def _shrink(match):
        url = match.group(0)
        if len(url) <= max_len:
            return url
        scheme, _, after = url.partition("://")
        host = after.split("/", 1)[0].split("?", 1)[0]
        elided = len(url) - len(scheme) - len("://") - len(host)
        itag = _ITAG_RE.search(url)
        tag = f"itag {itag.group(1)}, " if itag else ""
        return f"{scheme}://{host}/…({tag}+{elided} chars elided)"
    return _URL_RE.sub(_shrink, text)


def normalize_for_dedup(text):
    """A dedup key that ignores the volatile parts of a repeated line: every URL
    becomes <url> and every digit run becomes <n>, so the same error with a
    different expired URL / timestamp maps to one key. Pure."""
    return _DIGITS_RE.sub("<n>", _URL_RE.sub("<url>", text))


LINE_THROTTLE_RATE_MAX = 30
LINE_THROTTLE_WINDOW_S = 10.0
LINE_THROTTLE_SUMMARY_S = 30.0


class LineThrottle:
    """Per-stream throttle for pumped subprocess lines. Collapses consecutive
    duplicate-after-normalization lines (emitting a periodic '(last line repeated
    ×N)' at the line's own level, plus a '(previous line repeated ×N)' when the
    pattern changes) AND rate-limits distinct lines to rate_max per window_s (excess
    dropped, surfaced as a WARNING '(suppressed N lines)'). Pure given an injected
    monotonic clock. One instance per pump_subprocess call -> per feed, thread-isolated."""

    def __init__(self, rate_max=LINE_THROTTLE_RATE_MAX,
                 window_s=LINE_THROTTLE_WINDOW_S, summary_s=LINE_THROTTLE_SUMMARY_S):
        self.rate_max = rate_max
        self.window_s = window_s
        self.summary_s = summary_s
        self.last_key = None
        self.last_level = logging.INFO
        self.dup_count = 0
        self.last_summary_at = 0.0
        self.window_start = 0.0
        self.window_count = 0
        self.dropped_in_window = 0

    def emit(self, level, text, now):
        """Return the (level, text) records to log for one incoming line."""
        key = normalize_for_dedup(text)
        out = []
        if key == self.last_key:                       # consecutive duplicate
            self.dup_count += 1
            if now - self.last_summary_at >= self.summary_s:
                out.append((self.last_level, f"(last line repeated ×{self.dup_count})"))
                self.last_summary_at = now
            return out
        if self.dup_count > 0:                          # a new, distinct line ends a dup run
            out.append((self.last_level, f"(previous line repeated ×{self.dup_count})"))
            self.dup_count = 0
        self.last_key = key
        self.last_level = level
        self.last_summary_at = now
        if now - self.window_start >= self.window_s:    # roll the rate-limit window
            if self.dropped_in_window > 0:
                out.append((logging.WARNING, f"(suppressed {self.dropped_in_window} lines)"))
                self.dropped_in_window = 0
            self.window_start = now
            self.window_count = 0
        if self.window_count < self.rate_max:
            self.window_count += 1
            out.append((level, text))
        else:
            self.dropped_in_window += 1
        return out

    def flush(self, now):
        """Emit any pending summary at EOF so a trailing flood still reports its count."""
        out = []
        if self.dup_count > 0:
            out.append((self.last_level, f"(previous line repeated ×{self.dup_count})"))
            self.dup_count = 0
        if self.dropped_in_window > 0:
            out.append((logging.WARNING, f"(suppressed {self.dropped_in_window} lines)"))
            self.dropped_in_window = 0
        return out


def tag_line(source, line):
    """Prefix a single log line with its source tag for the merged view, stripping
    the trailing newline/carriage-return. chr(10)/chr(13) avoid a backslash escape
    inside the f-string expression (only allowed on Python 3.12+; the repo is 3.11)."""
    return f"[{source}] {line.rstrip(chr(10)).rstrip(chr(13))}"


def pump_subprocess(stream, logger, tag, on_line=None, now=time.monotonic):
    """Read text lines from a subprocess pipe (stream) and log each at a classified
    level, prefixed `[tag]`. Repeated lines are throttled and long URLs shortened
    (LineThrottle + shorten_urls) so a stuck retry loop can't flood the log; the
    first occurrence and periodic counts survive. When on_line is given, call it per
    (stripped) ORIGINAL line for side-channel parsing (e.g. feed quality) — a failing
    callback never breaks the pump. Runs to EOF; swallows read errors. Designed for a
    daemon thread."""
    throttle = LineThrottle()
    try:
        for raw in iter(stream.readline, ""):   # sentinel "" stops at EOF
            line = raw.rstrip("\n").rstrip("\r")
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:                # noqa: BLE001 — observer is best-effort
                    pass
            try:
                level = classify_subproc_line(line)   # classify the ORIGINAL line
                for lvl, text in throttle.emit(level, shorten_urls(line), now()):
                    logger.log(lvl, "[%s] %s", tag, text)
            except Exception:                    # noqa: BLE001 — throttling must never break the pump
                # Fallback logs the raw line at a fixed level: re-classifying here
                # could raise again (if classify was the failing call) and break the
                # pump thread, defeating the best-effort contract.
                logger.log(logging.ERROR, "[%s] %s", tag, line)
    except (ValueError, OSError):
        pass  # pipe closed mid-read — end the thread, never the daemon
    finally:
        try:
            for lvl, text in throttle.flush(now()):   # surface a trailing flood's count
                logger.log(lvl, "[%s] %s", tag, text)
        except Exception:                        # noqa: BLE001 — flush is best-effort too
            pass


def obs_log_dir(platform, home=None, env=None):
    """OBS Studio's log directory for a platform. Fixed-OS path -> string concat
    with '/', never os.path.join (see plan note). Returns the dir (may not exist)."""
    home = os.path.expanduser("~") if home is None else home
    env = os.environ if env is None else env
    if platform == "darwin":
        return home + "/Library/Application Support/obs-studio/logs"
    if platform.startswith("win") or platform == "nt":
        base = (env.get("APPDATA") or (home + "/AppData/Roaming")).replace("\\", "/")
        return base + "/obs-studio/logs"
    return home + "/.config/obs-studio/logs"


def list_logs(log_dir):
    """Regular files in log_dir, newest-first by mtime; [] if dir is absent."""
    try:
        files = [os.path.join(log_dir, f) for f in os.listdir(log_dir)]
    except OSError:
        return []
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=os.path.getmtime, reverse=True)
    return files


def newest_log(log_dir):
    """Newest file in log_dir, or None."""
    files = list_logs(log_dir)
    return files[0] if files else None


def archive_dates(log_dir, basenames):
    """Sorted-descending list of YYYY-MM-DD dates for which any of `basenames` has a
    rotated archive (`<basename>.<date>`) in log_dir."""
    dates = set()
    for path in list_logs(log_dir):
        name = os.path.basename(path)
        for base in basenames:
            m = re.fullmatch(re.escape(base) + r"\.(\d{4}-\d{2}-\d{2})", name)
            if m:
                dates.add(m.group(1))
    return sorted(dates, reverse=True)


def resolve_archive(log_dir, basename, date):
    """Realpath of the archive `<basename>.<date>` inside log_dir, or None. Guards
    traversal: `date` must be exactly YYYY-MM-DD, `basename` must carry no path
    separators, and the resolved path must stay inside log_dir."""
    if not date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return None
    if not basename or "/" in basename or "\\" in basename or os.sep in basename:
        return None
    root = os.path.realpath(log_dir)
    full = os.path.realpath(os.path.join(log_dir, f"{basename}.{date}"))
    try:
        inside = os.path.commonpath([root, full]) == root
    except ValueError:
        return None   # different drives on Windows
    if not inside or not os.path.isfile(full):
        return None
    return full

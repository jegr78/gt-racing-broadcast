#!/usr/bin/env python3
"""Stdlib checks for the logging helper. Run: python3 tests/test_logs.py"""
import logging, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import logsetup as lg


def t_configure_logging_writes_timestamped_line(tmp):
    path = os.path.join(tmp, "logs", "relay.console.log")
    log = lg.configure_logging("test.relay.a", path, to_stdout=False)
    log.info("hello world")
    for h in log.handlers:
        h.flush()
    with open(path, encoding="utf-8") as fh:
        line = fh.read().strip()
    # "2026-06-18 12:00:00 INFO hello world"
    assert line.endswith("INFO hello world"), line
    assert line[:4].isdigit() and line[4] == "-", line   # leading ISO date


def t_configure_logging_no_stdout_handler_when_not_tty(tmp):
    path = os.path.join(tmp, "logs", "b.log")
    log = lg.configure_logging("test.relay.b", path, to_stdout=False)
    assert all(not isinstance(h, logging.StreamHandler)
               or isinstance(h, logging.FileHandler)
               for h in log.handlers)


def t_configure_logging_idempotent(tmp):
    path = os.path.join(tmp, "logs", "c.log")
    a = lg.configure_logging("test.relay.c", path, to_stdout=False)
    n = len(a.handlers)
    b = lg.configure_logging("test.relay.c", path, to_stdout=False)
    assert a is b and len(b.handlers) == n   # no duplicate handlers


def t_rotation_survives_locked_file_no_line_lost(tmp):
    """Windows: a rollover whose rename fails because another process holds the
    file open (the Control Center tails relay.console.log / feed_*.log live —
    WinError 32) must NOT drop the log record. The handler keeps writing to the
    base file and defers the rollover instead of letting the line escape to
    stderr. Simulated by forcing a rollover and making rotate() raise."""
    path = os.path.join(tmp, "logs", "rot.log")
    log = lg.configure_logging("test.rotlock", path, to_stdout=False)
    handler = next(h for h in log.handlers if getattr(h, "_racecast", False))
    # Force the next emit to attempt a rollover, and make the rename fail the way
    # Windows does when the file is held open by another process.
    handler.rolloverAt = 1                       # in the past -> shouldRollover True
    def boom(src, dst):
        raise PermissionError(32, "The process cannot access the file")
    handler.rotate = boom
    log.warning("must survive the failed rollover")
    for h in log.handlers:
        h.flush()
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    assert "WARNING must survive the failed rollover" in body, body   # line not lost
    assert handler.rolloverAt > time.time(), handler.rolloverAt       # rollover deferred
    # A second line still lands too (no per-emit re-attempt storm).
    log.warning("second line still written")
    for h in log.handlers:
        h.flush()
    with open(path, encoding="utf-8") as fh:
        assert "second line still written" in fh.read()


def t_read_new_lines_incremental_and_rotation(tmp):
    """The re-open-per-poll tail reader: returns whole lines appended since the
    last byte offset, holds back a half-written trailing line, and restarts from
    the top when the file is rotated/truncated (so it never keeps the file open
    and blocks the writer's rename on Windows)."""
    p = os.path.join(tmp, "rnl.log")
    with open(p, "wb") as fh:
        fh.write(b"alpha\nbeta\n")
    lines, pos = lg.read_new_lines(p, 0)
    assert lines == ["alpha", "beta"] and pos == 11, (lines, pos)
    # nothing new -> empty, offset unchanged
    lines, pos2 = lg.read_new_lines(p, pos)
    assert lines == [] and pos2 == pos, (lines, pos2)
    # a full line + a partial (no trailing newline) -> only the full line
    with open(p, "ab") as fh:
        fh.write(b"gamma\npar")
    lines, pos = lg.read_new_lines(p, pos)
    assert lines == ["gamma"], lines
    # completing the partial line delivers it whole
    with open(p, "ab") as fh:
        fh.write(b"tial\n")
    lines, pos = lg.read_new_lines(p, pos)
    assert lines == ["partial"], lines
    # rotation/truncation: file replaced by a shorter one -> re-read from the top
    with open(p, "wb") as fh:
        fh.write(b"fresh\n")
    lines, pos = lg.read_new_lines(p, pos)
    assert lines == ["fresh"], lines


def t_read_new_lines_missing_file(tmp):
    assert lg.read_new_lines(os.path.join(tmp, "nope.log"), 0) == ([], 0)


def t_prune_removes_only_old_files(tmp):
    d = os.path.join(tmp, "prune"); os.makedirs(d)
    now = 1_000_000_000
    fresh = os.path.join(d, "relay.console.log")
    old = os.path.join(d, "relay.console.log.2020-01-01")
    for p in (fresh, old):
        open(p, "w").close()
    os.utime(fresh, (now - 1 * 86400, now - 1 * 86400))    # 1 day old -> keep
    os.utime(old, (now - 30 * 86400, now - 30 * 86400))    # 30 days old -> delete
    removed = lg.prune_old_logs(d, keep_days=7, now_ts=now)
    assert removed == [old], removed
    assert os.path.exists(fresh) and not os.path.exists(old)


def t_prune_missing_dir_is_noop():
    assert lg.prune_old_logs("/no/such/dir/xyz", keep_days=7, now_ts=1) == []


def t_classify_subproc_line_levels():
    assert lg.classify_subproc_line("HTTP 403 Forbidden") == logging.ERROR
    assert lg.classify_subproc_line("Traceback (most recent call last)") == logging.ERROR
    assert lg.classify_subproc_line("Waiting for streams, retrying in 5s") == logging.WARNING
    assert lg.classify_subproc_line("Opening stream: 1080p (hls)") == logging.INFO


def t_shorten_urls_elides_long_url_keeps_itag():
    url = ("https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/1781/"
           "itag/301/sig/SECRETSIG/lsig/SECRETLSIG/playlist/index.m3u8" + "z" * 120)
    line = "Unable to open URL: " + url + " (403 Forbidden)"
    out = lg.shorten_urls(line)
    # Host kept, path+query (incl. the sig/lsig tokens) dropped. Asserted on a
    # dotless fragment, not the full domain, so it is not an incomplete-URL-
    # sanitization pattern (CodeQL py/incomplete-url-substring-sanitization).
    assert "googlevideo" in out
    assert "itag 301" in out
    assert "SECRETSIG" not in out and "SECRETLSIG" not in out   # tokens elided
    assert "(403 Forbidden)" in out                             # non-URL text preserved
    assert len(out) < len(line)


def t_shorten_urls_leaves_short_url_and_plain_text():
    assert lg.shorten_urls("see http://h/x now") == "see http://h/x now"
    assert lg.shorten_urls("no url at all") == "no url at all"


def t_shorten_urls_handles_two_long_urls():
    u = "https://manifest.googlevideo.com/path/" + "a" * 200
    out = lg.shorten_urls("open " + u + " for url: " + u)
    assert out.count("/…(") == 2          # both long URLs shortened in place
    assert ("a" * 200) not in out


def t_normalize_for_dedup_collapses_url_and_digits():
    a = "Unable to open URL: https://x.com/expire/111/sig/AAA (403 Forbidden)"
    b = "Unable to open URL: https://y.com/expire/999/sig/BBB (403 Forbidden)"
    assert lg.normalize_for_dedup(a) == lg.normalize_for_dedup(b)
    assert lg.normalize_for_dedup("alpha line") != lg.normalize_for_dedup("beta line")


def t_tag_line_prefixes_and_strips_eol():
    assert lg.tag_line("feed_A", "serving stint 3\n") == "[feed_A] serving stint 3"
    assert lg.tag_line("relay", "x\r\n") == "[relay] x"


def t_pump_subprocess_logs_each_line(tmp):
    import io
    path = os.path.join(tmp, "logs", "feed_A.log")
    log = lg.configure_logging("test.pump", path, to_stdout=False)
    stream = io.StringIO("Opening stream\nHTTP 403 Forbidden\n")
    lg.pump_subprocess(stream, log, "streamlink")
    for h in log.handlers:
        h.flush()
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    assert "INFO [streamlink] Opening stream" in body, body
    assert "ERROR [streamlink] HTTP 403 Forbidden" in body, body


def t_obs_log_dir_per_platform():
    assert lg.obs_log_dir("darwin", home="/Users/x") == \
        "/Users/x/Library/Application Support/obs-studio/logs"
    assert lg.obs_log_dir("linux", home="/home/x") == "/home/x/.config/obs-studio/logs"
    assert lg.obs_log_dir("win32", home="/h", env={"APPDATA": "C:/Users/x/AppData/Roaming"}) \
        == "C:/Users/x/AppData/Roaming/obs-studio/logs"


def t_list_and_newest_log_order(tmp):
    d = os.path.join(tmp, "ll"); os.makedirs(d)
    a = os.path.join(d, "a.log"); b = os.path.join(d, "b.log")
    open(a, "w").close(); open(b, "w").close()
    os.utime(a, (100, 100)); os.utime(b, (200, 200))
    assert lg.list_logs(d) == [b, a]          # newest first
    assert lg.newest_log(d) == b
    assert lg.newest_log(os.path.join(tmp, "empty")) is None


def t_archive_dates_lists_rotated(tmp):
    d = os.path.join(tmp, "ad"); os.makedirs(d)
    for n in ("relay.console.log", "relay.console.log.2026-06-17",
              "relay.console.log.2026-06-16", "feed_A.log.2026-06-17", "junk.txt"):
        open(os.path.join(d, n), "w").close()
    assert lg.archive_dates(d, ["relay.console.log", "feed_A.log"]) == \
        ["2026-06-17", "2026-06-16"]


def t_resolve_archive_ok_and_guards(tmp):
    d = os.path.join(tmp, "ra"); os.makedirs(d)
    good = os.path.join(d, "relay.console.log.2026-06-17")
    open(good, "w").close()
    assert lg.resolve_archive(d, "relay.console.log", "2026-06-17") == os.path.realpath(good)
    assert lg.resolve_archive(d, "relay.console.log", "2026-06-18") is None   # no file
    assert lg.resolve_archive(d, "relay.console.log", "../../etc/passwd") is None
    assert lg.resolve_archive(d, "../relay.console.log", "2026-06-17") is None
    assert lg.resolve_archive(d, "relay.console.log", "2026-13-99x") is None   # bad date shape


def t_close_logging_releases_handlers(tmp):
    path = os.path.join(tmp, "logs", "close.log")
    log = lg.configure_logging("test.close", path, to_stdout=False)
    log.info("x")
    assert any(getattr(h, "_racecast", False) for h in log.handlers)
    lg.close_logging("test.close")
    assert not any(getattr(h, "_racecast", False) for h in log.handlers)


def t_pump_subprocess_on_line_hook():
    import io
    seen = []
    stream = io.StringIO("a\nb\n")
    logger = logging.getLogger("t.pump.hook"); logger.addHandler(logging.NullHandler())
    lg.pump_subprocess(stream, logger, "streamlink", on_line=seen.append)
    assert seen == ["a", "b"]
    # A failing callback never breaks the pump.
    stream2 = io.StringIO("x\n")
    def boom(_): raise ValueError("nope")
    lg.pump_subprocess(stream2, logger, "streamlink", on_line=boom)  # no raise


def t_pump_throttles_identical_flood_and_strips_tokens():
    import io
    records = []

    class _Cap(logging.Handler):
        def emit(self, r):
            records.append((r.levelno, r.getMessage()))

    logger = logging.getLogger("t.pump.flood")
    logger.handlers = [_Cap()]
    logger.setLevel(logging.DEBUG)
    url = "https://manifest.googlevideo.com/itag/301/sig/SECRETTOKEN/" + "z" * 200
    one = "[cli][error] Unable to fetch new streams: Unable to open URL: " + url + " (403 Forbidden)\n"
    seen = []
    lg.pump_subprocess(io.StringIO(one * 500), logger, "streamlink",
                       on_line=seen.append, now=lambda: 1000.0)
    msgs = [m for _lvl, m in records]
    assert len(records) <= 4                              # 500 identical -> a handful
    assert any("repeated ×499" in m for m in msgs)        # the rest counted
    assert all("SECRETTOKEN" not in m for m in msgs)      # manifest token stripped
    assert len(seen) == 500                               # on_line saw every original line


def t_pump_falls_back_to_raw_line_when_throttle_raises():
    import io
    records = []

    class _Cap(logging.Handler):
        def emit(self, r):
            records.append(r.getMessage())

    logger = logging.getLogger("t.pump.fallback")
    logger.handlers = [_Cap()]
    logger.setLevel(logging.DEBUG)
    orig = lg.shorten_urls
    lg.shorten_urls = lambda _t: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        lg.pump_subprocess(io.StringIO("alpha\nbeta\n"), logger, "streamlink")
    finally:
        lg.shorten_urls = orig
    assert "[streamlink] alpha" in records      # raw line logged despite the failure
    assert "[streamlink] beta" in records       # the pump kept going to the next line


def t_throttle_collapses_identical_flood():
    th = lg.LineThrottle()
    out = []
    for _ in range(1000):
        out += th.emit(logging.ERROR, "Unable to open URL: x (403)", 1000.0)
    out += th.flush(1000.0)
    texts = [t for _lvl, t in out]
    assert texts[0] == "Unable to open URL: x (403)"      # first occurrence emitted
    assert any("repeated ×999" in t for t in texts)       # the rest counted
    assert len(out) <= 3                                  # ~one real line + a summary
    assert all(lvl == logging.ERROR for lvl, _t in out)   # summary keeps the flood's level


def t_throttle_rate_limits_distinct_lines():
    th = lg.LineThrottle(rate_max=5, window_s=10.0, summary_s=30.0)
    out = []
    for i in range(20):
        out += th.emit(logging.INFO, "line " + chr(97 + i) + " alpha", 1000.0)
    out += th.flush(1000.0)
    emitted = [t for _lvl, t in out if "suppressed" not in t]
    assert len(emitted) == 5                              # capped at rate_max in the window
    assert any(lvl == logging.WARNING and "suppressed 15 lines" in t for lvl, t in out)


def t_throttle_flushes_dup_summary_on_pattern_change():
    th = lg.LineThrottle()
    out = []
    out += th.emit(logging.WARNING, "retrying connection", 1000.0)
    for _ in range(4):
        out += th.emit(logging.WARNING, "retrying connection", 1000.0)
    out += th.emit(logging.INFO, "stream opened", 1000.0)
    texts = [t for _lvl, t in out]
    assert texts == ["retrying connection", "(previous line repeated ×4)", "stream opened"]


def t_throttle_periodic_summary_while_flooding():
    th = lg.LineThrottle(summary_s=30.0)
    out = []
    out += th.emit(logging.ERROR, "boom", 1000.0)         # emitted
    out += th.emit(logging.ERROR, "boom", 1010.0)         # dup, 10s < 30 -> no summary
    out += th.emit(logging.ERROR, "boom", 1035.0)         # dup, 35s >= 30 -> summary
    texts = [t for _lvl, t in out]
    assert texts == ["boom", "(last line repeated ×2)"]


def t_harden_stdio_makes_a_narrow_console_survivable():
    # A narrow console codepage makes our own non-ASCII output raise instead of print.
    class _Narrow:
        """A stream that mimics a cp1252 console: it records reconfigure() rather than
        applying it, because a real console stream is the thing we cannot change here."""
        def __init__(self):
            self.encoding, self.errors, self.calls = "cp1252", "strict", []
        def reconfigure(self, **kw):
            self.calls.append(kw)
            self.errors = kw.get("errors", self.errors)

    out, err = _Narrow(), _Narrow()
    env = {}
    lg.harden_stdio(streams=(out, err), environ=env)
    for name, stream in (("stdout", out), ("stderr", err)):
        assert stream.calls, f"{name} was never reconfigured"
        assert stream.calls[-1].get("errors") == "replace", stream.calls
        # utf-8, not the console's own encoding: issue #24 settled this. Whenever stdout
        # is a PIPE — every Control Center job — Python picks the locale encoding and
        # those captured bytes are rendered in a UTF-8 web UI.
        assert stream.calls[-1].get("encoding") == "utf-8", stream.calls

    # The same leniency must reach every CHILD process, whatever spawns it. Setting it
    # in our own environment is what covers all 17 entrypoints at once instead of
    # editing each spawn site.
    assert env.get("PYTHONIOENCODING") == "utf-8:replace", env


def t_harden_stdio_never_raises_and_never_overrides_the_operator():
    # It runs before anything else in main(), so it must not be able to break a start.
    class _Hostile:
        encoding = "cp1252"
        def reconfigure(self, **kw):
            raise OSError("detached console")
    lg.harden_stdio(streams=(_Hostile(),), environ={})            # must not raise

    # An operator who set PYTHONIOENCODING deliberately keeps it.
    env = {"PYTHONIOENCODING": "utf-8"}
    lg.harden_stdio(streams=(), environ=env)
    assert env["PYTHONIOENCODING"] == "utf-8", env


SHIPPED = None          # filled by _shipped_sources()


def _shipped_sources():
    """Every shipped .py under src/. tools/ is maintainer-only and never runs on a
    producer's console, so it is deliberately out of scope."""
    global SHIPPED
    if SHIPPED is None:
        import pathlib
        SHIPPED = sorted(pathlib.Path(ROOT, "src").rglob("*.py"))
    return SHIPPED


def _calls(src, pattern):
    """Each call matching `pattern`, flattened to its full parenthesised span."""
    import re
    out = []
    for m in re.finditer(pattern, src):
        depth, i = 0, m.end() - 1
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((src[:m.start()].count("\n") + 1, src[m.start():i + 1]))
    return out


def t_every_text_subprocess_under_src_decodes_leniently():
    # subprocess reads pipes in a THREAD, so a decode failure never reaches the caller's
    # except: a traceback, and the output silently lost. Scope is the whole shipped tree
    # and every call form that decodes — a narrower guard missed five call sites.
    offenders = []
    for path in _shipped_sources():
        src = path.read_text(encoding="utf-8")
        pattern = r"(?:subprocess\.(?:run|Popen|check_output)|\.communicate)\s*\("
        for line, call in _calls(src, pattern):
            decodes = ("text=True" in call or "universal_newlines=True" in call
                       or "encoding=" in call)
            if decodes and "errors=" not in call:
                offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        "text-mode subprocess calls without errors=: " + ", ".join(offenders))


def t_no_argparse_help_string_carries_non_ascii():
    # argparse builds the whole help before printing, so one non-ASCII character kills
    # --help on a narrow console. "?" where an arrow should be is worse than "->".
    import re
    offenders = []
    for path in _shipped_sources():
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r'help=(["\'])(.*?)\1', src, re.S):
            bad = sorted({c for c in m.group(2) if ord(c) > 127})
            if bad:
                offenders.append(f"{path.name}:{src[:m.start()].count(chr(10)) + 1} {bad}")
    assert not offenders, "non-ASCII in argparse help: " + ", ".join(offenders)


def t_the_relay_hardens_stdio_before_it_builds_its_help():
    # Started directly as well as spawned, so inheriting PYTHONIOENCODING is not enough.
    # The order is the point: it crashed while argparse assembled the help.
    with open(os.path.join(ROOT, "src", "relay", "racecast-feeds.py"),
              encoding="utf-8") as fh:
        src = fh.read()
    body = src[src.index("\ndef main():"):]
    harden = body.index("harden_stdio(")
    parser = body.index("argparse.ArgumentParser(")
    assert harden < parser, "harden_stdio() must run before the parser is built"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in sorted(globals().items()):
            if name.startswith("t_") and callable(fn):
                import inspect
                fn(tmp) if inspect.signature(fn).parameters else fn()
                print("ok", name)
        # Windows can't delete a file with an open handle — release every rotating
        # handler this run attached before the temp dir is removed.
        for _ln in list(logging.Logger.manager.loggerDict):
            lg.close_logging(_ln)

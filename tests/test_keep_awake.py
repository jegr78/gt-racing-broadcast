#!/usr/bin/env python3
"""Idle-sleep block held while the relay runs (#1105).
Run: python3 tests/test_keep_awake.py"""
import importlib.util, logging, os, sys, tempfile, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import keep_awake as ka  # noqa: E402

PATHS = {"caffeinate": "/usr/bin/caffeinate", "systemd-inhibit": "/usr/bin/systemd-inhibit",
         "tail": "/usr/bin/tail"}


def which_from(found):
    return found.get


class ListLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def logger():
    log = logging.getLogger(f"keep-awake-test-{id(object())}")
    log.propagate = False
    handler = ListLog()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    return log, handler.records


class FakeProc:
    def __init__(self, exit_code=None, stderr=""):
        self.exit_code = exit_code
        self.stderr_text = stderr
        self.terminated = False
        self.done = threading.Event()
        if exit_code is not None:
            self.done.set()

    def poll(self):
        return self.exit_code

    def communicate(self):
        self.done.wait(5)
        return "", self.stderr_text

    def terminate(self):
        self.terminated = True
        self.exit_code = -15
        self.done.set()


class FakeKernel32:
    def __init__(self, result=1):
        self.result = result
        self.calls = []

    def SetThreadExecutionState(self, flags):
        self.calls.append((flags, threading.get_ident()))
        return self.result


def t_enabled_unless_explicitly_off():
    assert ka.enabled({}) is True
    assert ka.enabled({"RACECAST_KEEP_AWAKE": "1"}) is True
    for off in ("0", "false", "no", "off", " OFF "):
        assert ka.enabled({"RACECAST_KEEP_AWAKE": off}) is False, off


def t_macos_command_ends_with_the_relay():
    argv, note = ka.command("darwin", 4242, which_from(PATHS))
    assert argv == ["/usr/bin/caffeinate", "-i", "-w", "4242"], argv
    assert note == ""


def t_linux_command_inhibits_idle_and_sleep_while_the_pid_lives():
    argv, note = ka.command("linux", 4242, which_from(PATHS))
    assert argv == ["/usr/bin/systemd-inhibit", "--what=idle:sleep", "--mode=block",
                    "--who=racecast", "--why=relay running",
                    "/usr/bin/tail", "--pid=4242", "-f", "/dev/null"], argv


def t_missing_helpers_give_no_command_and_a_reason():
    argv, note = ka.command("linux", 1, which_from({"tail": "/usr/bin/tail"}))
    assert argv is None and "systemd-inhibit not found" in note, note
    argv, note = ka.command("linux", 1, which_from({"systemd-inhibit": "/x"}))
    assert argv is None and "tail not found" in note, note
    argv, note = ka.command("darwin", 1, which_from({}))
    assert argv is None and "caffeinate not found" in note, note
    argv, note = ka.command("sunos5", 1, which_from(PATHS))
    assert argv is None and "unsupported" in note, note


def t_windows_holds_the_flag_on_a_dedicated_thread_and_clears_it_on_stop():
    k = FakeKernel32()
    log, records = logger()
    holder = ka.KeepAwake(4242, platform="win32", kernel32=k, environ={}, log=log)
    holder.start()
    st = holder.status()
    assert st == {"held": True, "method": "SetThreadExecutionState", "note": ""}, st
    assert k.calls[0][0] == ka.ES_CONTINUOUS | ka.ES_SYSTEM_REQUIRED, k.calls
    assert k.calls[0][1] != threading.get_ident(), "the flag belongs to a long-lived thread"
    holder.stop()
    assert k.calls[-1] == (ka.ES_CONTINUOUS, k.calls[0][1]), \
        f"stop clears the flag on the thread that set it: {k.calls}"
    assert holder.status()["held"] is False
    assert not [r for r in records if r.levelno >= logging.WARNING], records


def t_windows_refusal_logs_once_and_never_raises():
    log, records = logger()
    holder = ka.KeepAwake(1, platform="win32", kernel32=FakeKernel32(result=0),
                          environ={}, log=log)
    holder.start()
    st = holder.status()
    assert st["held"] is False and "refused" in st["note"], st
    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, warnings
    holder.stop()


def t_helper_process_held_while_it_runs_and_terminated_on_stop():
    proc = FakeProc()
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return proc

    log, records = logger()
    holder = ka.KeepAwake(4242, platform="darwin", which=which_from(PATHS), popen=popen,
                          environ={}, log=log)
    holder.start()
    assert seen["argv"][:2] == ["/usr/bin/caffeinate", "-i"], seen
    assert seen["kw"].get("errors") == "replace", seen["kw"]
    assert holder.status() == {"held": True, "method": "caffeinate", "note": ""}
    holder.stop()
    assert proc.terminated is True
    assert holder.status()["held"] is False
    assert not [r for r in records if r.levelno >= logging.WARNING], records


def t_helper_that_exits_early_is_one_warning_and_not_held():
    proc = FakeProc(exit_code=1, stderr="Failed to inhibit: Access denied\n")
    log, records = logger()
    holder = ka.KeepAwake(1, platform="linux", which=which_from(PATHS),
                          popen=lambda argv, **kw: proc, environ={}, log=log)
    holder.start()
    st = holder.status()
    assert st["held"] is False and st["method"] == "systemd-inhibit", st
    assert "Access denied" in st["note"], st
    assert len(records) == 1 and records[0].levelno == logging.WARNING, \
        f"a helper that fails at once logs only its failure: {records}"
    assert "Access denied" in records[0].getMessage(), records


def t_popen_failure_is_one_warning_and_not_held():
    def popen(argv, **kw):
        raise OSError("exec format error")

    log, records = logger()
    holder = ka.KeepAwake(1, platform="linux", which=which_from(PATHS), popen=popen,
                          environ={}, log=log)
    holder.start()
    st = holder.status()
    assert st["held"] is False and "exec format error" in st["note"], st
    assert len([r for r in records if r.levelno >= logging.WARNING]) == 1, records
    holder.stop()


def t_missing_helper_logs_once_and_continues():
    log, records = logger()
    holder = ka.KeepAwake(1, platform="linux", which=which_from({}), environ={}, log=log)
    holder.start()
    st = holder.status()
    assert st["held"] is False and "systemd-inhibit not found" in st["note"], st
    assert len(records) == 1, records


def t_switch_off_starts_nothing():
    def popen(argv, **kw):
        raise AssertionError("no helper may start when the switch is off")

    log, records = logger()
    holder = ka.KeepAwake(1, platform="darwin", which=which_from(PATHS), popen=popen,
                          environ={"RACECAST_KEEP_AWAKE": "0"}, log=log)
    holder.start()
    st = holder.status()
    assert st == {"held": False, "method": None, "note": "RACECAST_KEEP_AWAKE=0"}, st


def _load_relay():
    spec = importlib.util.spec_from_file_location(
        "irofeeds_keep_awake", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _StubSource:
    def get(self):
        return ["a", "b"]

    def get_rows(self):
        return [("a", "", "", 1), ("b", "", "", 2)]

    def health(self):
        return {"count": 2, "last_ok_age_s": 0, "last_error": None}


def t_relay_status_reports_the_block_in_race_and_solo():
    rf = _load_relay()
    logdir = tempfile.mkdtemp(prefix="racecast-test-keep-awake-")
    held = {"held": True, "method": "caffeinate", "note": ""}

    class Fake:
        def status(self):
            return held

    race = rf.Relay(_StubSource(), (53001, 53002), logdir)
    assert race.status().get("keep_awake") == {"held": False, "method": None,
                                               "note": "not started"}, "race before start"
    race.keep_awake = Fake()
    assert race.status().get("keep_awake") == held, "race status reports the block"
    solo = rf.Relay(None, [], logdir, solo=True)
    solo.keep_awake = Fake()
    assert solo.status().get("keep_awake") == held, "solo status reports the block"


def t_console_status_keeps_only_the_held_flag_for_crew_roles():
    rf = _load_relay()
    full = {"feeds": {}, "keep_awake": {"held": False, "method": "systemd-inhibit",
                                        "note": "Failed to inhibit: Access denied"}}
    crew = rf.redact_console_status(full, ["commentator"])
    assert crew["keep_awake"] == {"held": False}, crew
    director = rf.redact_console_status(full, ["director"])
    assert director["keep_awake"] == full["keep_awake"], director


def t_control_center_relay_row_shows_the_block():
    with open(os.path.join(ROOT, "src", "ui", "control-center.html"), encoding="utf-8") as fh:
        html = fh.read()
    row = html[html.index("setBadge('relay', relayUp"):]
    row = row[:row.index(";")]
    assert "keepAwakeText(s.relay.keep_awake)" in row, "the relay row names the idle-sleep block"
    fn = html[html.index("function keepAwakeText(k)"):]
    fn = fn[:fn.index("\n}")]
    assert "idle sleep NOT blocked" in fn and "k.note" in fn, fn


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

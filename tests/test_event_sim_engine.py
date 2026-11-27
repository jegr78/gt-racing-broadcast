#!/usr/bin/env python3
"""The event-simulation engine (#1232): scenario loading, the expect language, the step
runner on a fake clock, the report and the stack's pure helpers. Nothing is spawned.
Run: python3 tests/test_event_sim_engine.py"""
import atexit
import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = os.path.join(ROOT, "tools", "event-sim")
for _p in (SIM, os.path.join(SIM, "fakes"), os.path.join(ROOT, "src", "scripts")):
    sys.path.insert(0, _p)
import engine  # noqa: E402
import event_sim  # noqa: E402
import expect  # noqa: E402
import probes  # noqa: E402
import report  # noqa: E402
import roles  # noqa: E402
import scenario  # noqa: E402
import sheet_sim  # noqa: E402
import stack  # noqa: E402
import webhook_sink  # noqa: E402
import roles.builtin  # noqa: E402

builtin = roles.builtin

REG = roles.default_registry()


class Clock:
    """A fake monotonic clock; sleeping advances it."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += max(s, 0.001)


class Box(expect.Probe):
    fields = ("value", "data")
    calls = ("twice", "kind")

    def __init__(self, value=0, data=None):
        self.value, self.data = value, expect.View(data if data is not None else {})

    def twice(self, n):
        return n * 2

    def kind(self, v):
        return type(v).__name__


class Recorder(roles.Role):
    """A role whose actions record calls; `fail` raises, `bump` moves a probe value."""
    kind = "rec"
    log = []

    def act_go(self, args):
        Recorder.log.append((self.name, args))
        return {"id": 7, "nested": {"k": "v"}}

    def act_fail(self, args):
        raise roles.StepError("button missing")

    def act_bump(self, args):
        self.ctx.box.value = args.get("to", 1)

    def act_note(self, args):
        self.ctx.finding("soft", "no visible feedback")

    def shot(self, path, element=""):
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")


def _reg():
    return dict(REG, rec=Recorder)


ROOTS = (*expect.ROOTS, "box")


def _scn(steps, **top):
    data = dict(top, step=steps)
    return scenario.from_dict(data, _reg(), name="t", roots=ROOTS)


def _run(steps, box=None, race=None, **top):
    Recorder.log = []
    clock = Clock()
    ctx = engine.RunContext()
    ctx.box = box or Box()
    probes_ = {"box": ctx.box, **({"race": race} if race else {})}
    tmp = tempfile.mkdtemp(prefix="event-sim-test-")
    atexit.register(shutil.rmtree, tmp, True)
    eng = engine.Engine(_scn(steps, **top), ctx, probes_, registry=_reg(), clock=clock,
                        sleep=clock.sleep, shots_dir=os.path.join(tmp, "shots"))
    return eng.run(), clock, tmp


# -- scenario --------------------------------------------------------------------
def t_durations_and_triggers():
    assert scenario.parse_duration("1m30s") == 90.0
    assert scenario.parse_duration("500ms") == 0.5
    for bad in ("", "5", "5x", "s5", "1m 30s", 5):
        try:
            scenario.parse_duration(bad)
        except scenario.ScenarioError:
            continue
        raise AssertionError(f"{bad!r} must be refused")
    assert scenario.parse_at("after:start+20s") == scenario.Trigger("after", 20.0, ref="start")
    assert scenario.parse_at("lap:leader=3").condition == "race.lap.leader >= 3", \
        "the relay's race.lap is {leader, count}"
    assert scenario.parse_at("state:finishing").condition == "race.state == 'finishing'"
    assert scenario.parse_at(None).kind == "now"


def _refused(steps, needle, **top):
    try:
        _scn(steps, **top)
    except scenario.ScenarioError as exc:
        assert needle in str(exc), (needle, str(exc))
        return
    raise AssertionError(f"a scenario with {needle!r} must be refused")


def t_scenario_refuses_what_cannot_run():
    go = {"role": "rec", "do": "go"}
    _refused([{"role": "nobody", "do": "go"}], "unknown role")
    _refused([{"role": "rec", "do": "fly"}], "has no action 'fly'")
    _refused([go, go], "not unique")
    _refused([dict(go, at="after:later")], "not an earlier step")
    _refused([dict(go, id="../x")], "must match")
    _refused([dict(go, shot="../../etc")], "shot name")
    _refused([dict(go, expect=["box.__class__"])], "not allowed")
    _refused([dict(go, colour="red")], "unknown key")
    _refused([], "at least one")
    _refused([go, dict(go, id="b", args={"v": "${tpyo.id}"})], "refers to 'tpyo'")
    _refused([dict(go, args={"v": "${b.id}"}), dict(go, id="b")], "refers to 'b'")
    _refused([dict(go, id="a", args={"v": "${b.id}"}), dict(go, id="b", args={"v": "${a.id}"})], "refers to 'b'")
    _refused([dict(go, id="a.b")], "must match")
    for bad in ("ok\n", ".", "..", "CON", "nul", "Com1", "lpt9", "aux.png", "-x"):
        _refused([dict(go, shot=bad)], "shot name")
        _refused([dict(go, id=bad)], "id")
        _refused([go], "name", name=bad)
    for role in ("rec:../../x", "rec:a\n<script>", "rec:", "rec:" + "x" * 65):
        _refused([dict(go, role=role)], "role")
        _refused([dict(go, shot={"name": "s", "role": role})], "shot role")
    _scn([dict(go, role="rec:Comms 2", shot={"name": "tower-1.final", "role": "rec:Sim Director"})])
    _refused([go], "unknown top-level", extra=1)


def t_scenario_checks_the_event_and_its_tabs():
    go = [{"role": "rec", "do": "go"}]

    def comms(*rows):
        return {"event": {"commentators": list(rows)}}
    _refused(go, "channel must match", **comms({"name": "A", "channel": "../x"}))
    _refused(go, "used twice", **comms({"name": "A", "channel": "c1"}, {"name": "B", "channel": "C1"}))
    _refused(go, "name must be", **comms({"name": " ", "channel": "c1"}))
    _refused(go, "stint must be", **comms({"name": "A", "channel": "c1", "stint": 0}))
    _refused(go, "must be a list of rows", sheet={"tabs": {"Crew": "x"}})
    _refused(go, "cells must be", sheet={"tabs": {"Crew": [[{"a": 1}]]}})
    _refused([{"role": "rec", "do": "go", "expect": ["box.value == " + "1" * 500]}], "longer than")


def t_scenario_checks_the_event_keys_and_values_at_load():
    go = [{"role": "rec", "do": "go"}]
    _refused(go, "qualifing_part", event={"qualifing_part": True})
    _refused(go, "colour", event={"commentators": [{"name": "A", "channel": "c1", "colour": "red"}]})
    for key, bad in (("parts", 10), ("parts", "2"), ("classes", []), ("classes", ["GT3", " "]),
                     ("track", "Le Mans"), ("track", 123), ("cars", 0), ("cars", 16), ("cars", True),
                     ("stints", 0), ("stints", "3"), ("seed", "7"), ("seed", 1.5),
                     ("track_length_m", 10), ("track_length_m", "1200"), ("track_length_m", True),
                     ("track_length_m", float("nan")), ("track_length_m", float("inf")), ("track_length_m", 1e9),
                     ("track_length_m", 30001),
                     ("qualifying_part", "yes"), ("probe", "yes"), ("probe", 1)):
        _refused(go, f"event.{key}", event={key: bad})
    scn = _scn(go, event={"stints": 3, "track": "6e378b", "track_length_m": 1200, "cars": 6,
                          "classes": ["GT3", "GT4"], "parts": 2, "qualifying_part": True, "seed": 7,
                          "probe": True})
    assert scn.event["parts"] == 2


def t_a_commentator_step_names_a_commentator_of_the_scenario():
    event = {"commentators": [{"name": "Comms 1", "channel": "c1"}]}
    chat = {"role": "commentator:Comms 1", "do": "chat"}
    _scn([chat], event=event)
    _refused([dict(chat, role="commentator")], "commentator:<crew name>", event=event)
    _refused([dict(chat, role="commentator:Comms 9")], "'Comms 9' is no commentator", event=event)
    _refused([{"role": "rec", "do": "go", "shot": {"name": "s", "role": "commentator:Comms 9"}}],
             "'Comms 9' is no commentator", event=event)
    crew = [["Name", "Commentator", "Director"], ["Guest", "TRUE", "FALSE"], ["Boss", "FALSE", "TRUE"]]
    _scn([dict(chat, role="commentator:Guest")], event=event, sheet={"tabs": {"Crew": crew}})
    _refused([dict(chat, role="commentator:Boss")], "'Boss' is no commentator", event=event,
             sheet={"tabs": {"Crew": crew}})
    relay_rule = [["Person", "Commentator"], ["Yes Man", " yes "], ["Tick", "\u2713"], ["Ex", "x"]]
    for name in ("Yes Man", "Tick", "Ex"):
        _scn([dict(chat, role=f"commentator:{name}")], event=event, sheet={"tabs": {"Crew": relay_rule}})
    schedule = [["URL", "Streamer", "Stint"], ["", "Comms 1", "Stint 1"], ["", "Late Guest", "Stint 2"]]
    _scn([dict(chat, role="commentator:Late Guest")], event=event, sheet={"tabs": {"Schedule": schedule}})
    assert _scn([dict(chat, role="commentator:comms 1")], event=event).steps, "names compare as asset keys"
    dropped = {"Schedule": [["URL", "Streamer", "Stint"], ["", "Other", "Stint 1"]],
               "Crew": [["Name", "Commentator"], ["Other", "TRUE"]]}
    _refused([{"role": "rec", "do": "go"}], "event.commentators[0]", event=event, sheet={"tabs": dropped})


def t_film_keys_are_checked_at_load():
    view = {"role": "director", "do": "view", "args": {"area": "handover"}}
    read = {"id": "read", "role": "viewer", "do": "read"}
    scn = _scn([dict(view, caption="The director opens the handover", chapter="On air",
                     focus=["director", "program"]), read, {"id": "go", "role": "rec", "do": "go"}])
    first = scn.steps[0]
    assert (first.caption, first.chapter, first.focus) == \
        ("The director opens the handover", "On air", ["director", "program"]), "the keys reach the step"
    assert (scn.steps[1].caption, scn.steps[1].chapter, scn.steps[1].focus) == (None, None, None), \
        "a step without them carries none"
    for key in ("caption", "chapter"):
        for bad in ("", "  ", 3, ["x"]):
            _refused([dict(view, **{key: bad})], f"{key} must be a non-empty string")
    for bad in ("director", [], [3]):
        _refused([dict(view, focus=bad), read], "focus must be a list of view names")
    _refused([dict(view, focus=["director", "director"]), read], "names 'director' twice")
    _refused([dict(view, focus=["race_control"]), read], "'race_control' is no view of this run")
    _refused([dict(view, focus=["rec"]), read, {"id": "go", "role": "rec", "do": "go"}],
             "'rec' is no view of this run")
    assert _scn([view, dict(read, focus=["program"])]).steps[1].focus == ["program"], "program is always a view"
    assert _scn([dict(view, focus=["viewer"]), read]).steps[0].focus == ["viewer"], \
        "a view a later step opens counts too"


def t_film_cards_are_checked_at_load():
    view = {"role": "director", "do": "view", "args": {"area": "handover"}, "chapter": "Race day", "caption": "Go"}
    card = {"title": "Race day", "subtitle": "Three stints, two driver swaps"}
    title = {"title": "GT Racing Broadcast", "subtitle": "An endurance event"}
    scn = _scn([dict(view, card=card), {"id": "go", "role": "rec", "do": "go"}], intro=title, outro=title)
    assert scn.steps[0].card == card and scn.steps[1].card is None, "the card reaches its step, and only that one"
    assert (scn.intro, scn.outro) == (title, title), "intro and outro reach the scenario"
    assert (_scn([view]).intro, _scn([view]).outro) == (None, None), "a scenario without them has none"
    for bad in ("Race day", {"title": "Race day"}, {"title": "", "subtitle": "x"}, {"title": "x", "subtitle": 3},
                dict(card, colour="red")):
        _refused([dict(view, card=bad)], "card must be a table with a non-empty title and subtitle")
        _refused([view], "intro must be a table with a non-empty title and subtitle", intro=bad)
        _refused([view], "outro must be a table with a non-empty title and subtitle", outro=bad)
    _refused([{"role": "director", "do": "view", "caption": "Go", "card": card}],
             "a card opens a chapter: the step needs a chapter and a caption")
    _refused([{"role": "director", "do": "view", "chapter": "Race day", "card": card}],
             "a card opens a chapter: the step needs a chapter and a caption")


def t_a_focus_names_one_two_or_four_views():
    steps = [{"id": r, "role": r, "do": "open"} for r in ("director", "viewer", "producer", "race_control")]
    for views in (["director"], ["director", "viewer"], ["director", "program", "viewer", "producer"]):
        assert _scn([dict(steps[0], focus=views), *steps[1:]]).steps[0].focus == views, views
    for views in (["director", "program", "viewer"],
                  ["director", "program", "viewer", "producer", "race_control"]):
        _refused([dict(steps[0], focus=views), *steps[1:]], "1, 2 or 4 views")


def t_film_keys_change_nothing_outside_record_mode():
    plain = [{"id": "a", "role": "rec", "do": "go"}, {"id": "b", "at": "after:a", "role": "rec", "do": "go"}]
    film = [dict(plain[0], caption="Go", chapter="Start", focus=["program"], card={"title": "Go", "subtitle": "Now"}),
            dict(plain[1], caption="Again")]
    runs = [_run(steps)[0] for steps in (plain, film)]
    keep = ("id", "role", "do", "status", "result", "checks")
    assert [{k: s.get(k) for k in keep} for s in runs[0]["steps"]] == \
        [{k: s.get(k) for k in keep} for s in runs[1]["steps"]], "the keys change no step of a plain run"
    assert "caption" not in json.dumps(runs[1]) and "Again" not in json.dumps(runs[1]), \
        "a plain run's report carries no caption"
    assert "card" not in json.dumps(runs[1]) and "Now" not in json.dumps(runs[1]), "nor a card"


def t_role_instances_share_the_kind():
    scn = _scn([{"role": "rec:Comms 2", "do": "go"}])
    assert scenario.role_kind(scn.steps[0].role) == "rec"


def t_bundled_scenarios_load():
    names = [f[:-5] for f in os.listdir(os.path.join(SIM, "scenarios")) if f.endswith(".toml")]
    assert "smoke" in names, names
    for name in names:
        scn = scenario.load(scenario.find(name, os.path.join(SIM, "scenarios")), REG)
        assert scn.steps, name


def t_cli_check_validates_without_starting_anything():
    out = subprocess.run([sys.executable, os.path.join(SIM, "event_sim.py"), "--scenario", "smoke", "--check"],
                         capture_output=True, text=True, errors="replace", timeout=60)
    assert out.returncode == 0 and "valid" in out.stdout, out.stdout + out.stderr
    out = subprocess.run([sys.executable, os.path.join(SIM, "event_sim.py"), "--scenario", "nope", "--check"],
                         capture_output=True, text=True, errors="replace", timeout=60)
    assert out.returncode == 2 and "no scenario" in out.stderr, out.stderr


def t_repeat_runs_each_run_in_its_own_unit_one_after_the_other():
    scn = scenario.load(scenario.find("smoke", os.path.join(SIM, "scenarios")), REG)
    out = tempfile.mkdtemp(prefix="event-sim-repeat-")
    atexit.register(shutil.rmtree, out, True)
    entered, waited = [], []

    def enter(script, argv, seconds=None, long=False, stop_pipe=False):
        assert stop_pipe, "a stop signal reaches each contained run through its stop pipe"
        entered.append(argv)
        run_out = argv[argv.index("--out") + 1]
        if len(entered) == 2:
            raise RuntimeError("host under memory pressure: memory PSI full avg10 9% > 5%")
        os.makedirs(run_out)
        ok = len(entered) == 1
        report.write(run_out, {"scenario": "smoke", "description": "", "ok": ok, "duration": 1.0, "pages": {},
                               "steps": [{"id": "a", "role": "r", "do": "d", "status": "ok" if ok else "failed",
                                          "duration": 1.0, "error": "", "checks": [], "actions": []}],
                               "findings": []})
        return 0 if ok else 1
    args = event_sim.build_parser().parse_args(["--scenario", "smoke", "--out", out, "--repeat", "3", "--no-skips",
                                                "--load", "cpu:2", "--trace", "all"])
    code = event_sim.repeat(scn, out, args, enter=enter, wait=lambda: waited.append(1) or True)
    assert code == 1, "a run that failed or did not run fails the set"
    assert len(entered) == 3 and len(waited) == 3, "each run waits for the unit, then gets its own"
    for i, argv in enumerate(entered, 1):
        assert argv[argv.index("--out") + 1] == os.path.join(out, f"run-{i:02d}"), argv
        assert "--repeat" not in argv, "a run inside the unit is one run"
        assert argv[argv.index("--load") + 1] == "cpu:2" and argv[argv.index("--trace") + 1] == "all", argv
        assert "--no-skips" in argv, argv
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    assert summary["counts"] == {"pass": 1, "fail": 1, "not-run": 1}, summary["counts"]
    assert summary["meta"]["load"] == "cpu:2" and summary["meta"]["runs"] == 3, summary["meta"]
    for bad in (["--repeat", "0"], ["--repeat", "51"], ["--load", "cpu:9"], ["--trace", "some"]):
        try:
            with open(os.devnull, "w") as quiet:
                old, sys.stderr = sys.stderr, quiet
                try:
                    event_sim.build_parser().parse_args(["--scenario", "smoke", *bad])
                finally:
                    sys.stderr = old
            raise AssertionError(f"{bad} must be refused")
        except SystemExit:
            pass  # argparse refuses it


def t_a_stopped_repeat_set_still_summarizes_its_finished_runs():
    scn = scenario.load(scenario.find("smoke", os.path.join(SIM, "scenarios")), REG)
    out = tempfile.mkdtemp(prefix="event-sim-repeat-")
    atexit.register(shutil.rmtree, out, True)

    def enter(script, argv, seconds=None, long=False, stop_pipe=False):
        run_out = argv[argv.index("--out") + 1]
        if run_out.endswith("run-02"):
            raise event_sim.Stopped(signal.SIGTERM)
        report.write(run_out, {"scenario": "smoke", "description": "", "ok": True, "duration": 1.0, "pages": {},
                               "steps": [], "findings": []})
        return 0
    args = event_sim.build_parser().parse_args(["--scenario", "smoke", "--out", out, "--repeat", "3"])
    try:
        event_sim.repeat(scn, out, args, enter=enter, wait=lambda: True)
        raise AssertionError("a stop must end the set")
    except event_sim.Stopped:
        pass  # the set ends on the stop
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    assert summary["counts"] == {"pass": 1, "fail": 0, "not-run": 0} and summary["meta"]["stopped"] == "SIGTERM", \
        f"the summary holds the finished run and names the stop: {summary['counts']}, {summary['meta']}"


def t_repeat_never_reads_an_old_report_and_needs_exit_0_for_a_pass():
    scn = scenario.load(scenario.find("smoke", os.path.join(SIM, "scenarios")), REG)
    out = tempfile.mkdtemp(prefix="event-sim-repeat-")
    atexit.register(shutil.rmtree, out, True)
    old = os.path.join(out, "run-02")
    os.makedirs(old)
    report.write(old, {"scenario": "smoke", "description": "", "ok": True, "duration": 1.0, "pages": {},
                       "steps": [], "findings": []})
    entered = []

    def enter(script, argv, seconds=None, long=False, stop_pipe=False):
        entered.append(argv)
        return 3
    args = event_sim.build_parser().parse_args(["--scenario", "smoke", "--out", out, "--repeat", "2"])
    assert event_sim.repeat(scn, out, args, enter=enter, wait=lambda: True) == 2 and entered == [], \
        "an out dir that already holds run-02 is refused before any run: its report is not this set's"
    shutil.rmtree(old)

    def crashed_after_its_report(script, argv, seconds=None, long=False, stop_pipe=False):
        run_out = argv[argv.index("--out") + 1]
        os.makedirs(run_out)
        report.write(run_out, {"scenario": "smoke", "description": "", "ok": True, "duration": 1.0, "pages": {},
                               "steps": [], "findings": []})
        return 1
    args = event_sim.build_parser().parse_args(["--scenario", "smoke", "--out", out, "--repeat", "1"])
    assert event_sim.repeat(scn, out, args, enter=crashed_after_its_report, wait=lambda: True) == 1
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
        assert json.load(fh)["counts"] == {"pass": 0, "fail": 1, "not-run": 0}, "an ok report with exit 1 is a fail"


def t_a_run_owns_its_work_dir_and_names_it_in_its_out_dir():
    out = tempfile.mkdtemp(prefix="event-sim-out-")
    atexit.register(shutil.rmtree, out, True)
    before = os.environ.get("TMPDIR")
    with event_sim.owned_work(out) as work:
        assert os.path.isdir(work) and os.path.basename(work).startswith("racecast-sim-"), work
        with open(os.path.join(out, event_sim.WORK_NOTE), encoding="utf-8") as fh:
            assert fh.read().strip() == work, "the out dir names the run's own work dir, for a cleanup by path"
        assert os.environ["TMPDIR"].startswith(work), "the browser's temporary files live in the run's work dir"
    assert not os.path.exists(work) and not os.path.exists(os.path.join(out, event_sim.WORK_NOTE)), \
        "the run removes what it owns, and only that"
    assert os.environ.get("TMPDIR") == before, "TMPDIR is restored"
    with event_sim.owned_work(out, keep=True) as work:
        pass
    atexit.register(shutil.rmtree, work, True)
    assert os.path.isdir(work), "--keep-work keeps it"
    try:
        with event_sim.owned_work(out):
            raise AssertionError("an out dir that names a work dir already is refused")
    except RuntimeError as exc:
        assert work in str(exc), f"the refusal names the old work dir: {exc}"
    shutil.rmtree(work)
    os.remove(os.path.join(out, event_sim.WORK_NOTE))
    with event_sim.owned_work(out) as work:
        os.remove(os.path.join(out, event_sim.WORK_NOTE))
    assert not os.path.exists(work), "a note someone removed meanwhile does not break the cleanup"


class _StopStack:
    """stack.Stack without services; `on_start` runs in its start, `on_stop` inside the teardown."""
    on_start = None
    on_stop = None
    obs_of = None
    last = None

    def __init__(self, scn, work, **_):
        self.urls, self.token, self.stub, self.event_link = {}, None, None, None
        self.obs = _StopStack.obs_of() if _StopStack.obs_of else None
        self.kill_relay, self.log_checks, self.stopped = None, None, []
        self.work = work
        self.track_line, self.track_trace = "simulated lap", None
        _StopStack.last = self

    def start(self):
        if _StopStack.on_start:
            _StopStack.on_start()

    def run_secrets(self):
        return ()

    def probes(self):
        return {}

    def stop(self, logs_to=None):
        if _StopStack.on_stop:
            _StopStack.on_stop()
        self.stopped.append(logs_to)
        return []


class _QuietEngine:
    """An engine whose run ends at once."""

    def __init__(self, scn, ctx, *_, **__):
        self.scenario, self.results, self.start = scn, {}, None

    def run(self):
        return self.data([])

    def data(self, results):
        return {"scenario": self.scenario.name, "description": "", "ok": True, "duration": 1.0, "pages": {},
                "steps": [], "findings": []}


class _SignalledEngine:
    """An engine whose run receives `signum` from outside, then looks for a stop as the engine's loop does."""
    signum = None

    def __init__(self, scn, ctx, *_, interrupted=None, **__):
        self.scenario, self.results, self.start, self.interrupted = scn, {}, None, interrupted

    def run(self):
        signal.raise_signal(_SignalledEngine.signum)
        self.interrupted()
        raise AssertionError("the stop signal did not end the run")

    def data(self, results):
        return {"scenario": self.scenario.name, "description": "", "ok": True, "duration": 1.0, "pages": {},
                "steps": [], "findings": []}


def _signalled_run(signum, on_stop=None):
    """event_sim.run with a fake stack and engine whose run gets `signum`. -> (exc, out, stack)."""
    scn = scenario.load(scenario.find("smoke", os.path.join(SIM, "scenarios")), REG)
    out = tempfile.mkdtemp(prefix="event-sim-stop-")
    atexit.register(shutil.rmtree, out, True)
    old = stack.Stack, engine.Engine
    stack.Stack, engine.Engine = _StopStack, _SignalledEngine
    _SignalledEngine.signum, _StopStack.on_stop, _StopStack.last = signum, on_stop, None
    try:
        event_sim.run(scn, out)
    except KeyboardInterrupt as exc:
        return exc, out, _StopStack.last
    finally:
        (stack.Stack, engine.Engine), _StopStack.on_stop = old, None
    raise AssertionError("a stopped run must not end as a normal one")


def t_sigterm_stops_the_run_like_ctrl_c():
    before = signal.getsignal(signal.SIGTERM)
    exc, out, st = _signalled_run(signal.SIGTERM)
    assert getattr(exc, "signum", None) == signal.SIGTERM, f"the stop names its signal: {exc!r}"
    assert st.stopped == [os.path.join(out, "logs")], "the stack and its children are torn down"
    assert not os.path.exists(st.work) and not os.path.exists(os.path.join(out, event_sim.WORK_NOTE)), \
        "the work dir goes as on a normal stop"
    with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    assert not data["ok"] and "SIGTERM" in data["findings"][0]["message"], \
        f"the report says the run was stopped: {data['findings']}"
    assert signal.getsignal(signal.SIGTERM) == before, "the run's handlers are gone after it"


def t_a_second_stop_signal_does_not_cut_the_teardown_short():
    nums = [getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)]
    exc, _, st = _signalled_run(nums[-1], on_stop=lambda: [signal.raise_signal(n) for n in nums])
    assert st.stopped and exc.signum == nums[-1], "the teardown finishes despite more stop signals"


def t_an_inherited_ignored_sigterm_still_stops_the_run():
    old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        exc, _, st = _signalled_run(signal.SIGTERM)
        assert st.stopped and exc.signum == signal.SIGTERM, "an ignored SIGTERM still stops the run"
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN, "the ignored SIGTERM is restored after the run"
    finally:
        signal.signal(signal.SIGTERM, old)


def t_a_sigint_a_background_start_ignored_stops_the_run_again():
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        _, _, st = _signalled_run(signal.SIGINT)
        assert st.stopped and not os.path.exists(st.work), "Ctrl+C stops a background run and tears it down"
        assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN, "the ignored SIGINT is restored after the run"
    finally:
        signal.signal(signal.SIGINT, old)


def t_the_stop_pipe_closing_stops_a_contained_run():
    if os.name == "nt":
        return  # no stop pipe on Windows: no unit there
    r, w = os.pipe()
    try:
        with event_sim.stop_signals(stop_fd=r):
            os.close(w)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                time.sleep(0.05)
        raise AssertionError("the closed stop pipe did not stop the run")
    except event_sim.Stopped as exc:
        assert exc.signum == signal.SIGTERM, exc
    finally:
        os.close(r)


class _RealObs:
    recording = False

    def start_record(self):
        return 0.0


def t_a_stop_held_during_the_obs_quit_still_writes_the_report():
    exc = None
    scn = scenario.load(scenario.find("smoke", os.path.join(SIM, "scenarios")), REG)
    out = tempfile.mkdtemp(prefix="event-sim-stop-")
    atexit.register(shutil.rmtree, out, True)
    saved = stack.Stack, engine.Engine, event_sim.record.in_unit
    stack.Stack, engine.Engine, event_sim.record.in_unit = _StopStack, _QuietEngine, lambda *a: True
    _StopStack.on_stop = lambda: signal.raise_signal(signal.SIGTERM)
    _StopStack.obs_of = _RealObs
    try:
        event_sim.run(scn, out, record_dir=os.path.join(out, "rec"), obs="real")
        raise AssertionError("a SIGTERM during the teardown must still stop the run")
    except event_sim.Stopped as stop:
        exc = stop
    finally:
        (stack.Stack, engine.Engine, event_sim.record.in_unit), _StopStack.on_stop, _StopStack.obs_of = saved, None, None
    assert exc.signum == signal.SIGTERM, exc
    assert os.path.exists(os.path.join(out, "report.json")), "the held signal waits until the report is written"


class _ClosingBrowser:
    """browser.Browser without Chromium; `on_close` runs as it closes."""
    on_close = None

    def __init__(self, headless=True):
        self.closed = False

    def close(self):
        if _ClosingBrowser.on_close:
            _ClosingBrowser.on_close()
        self.closed = True


def _fake_run(engine_cls=_QuietEngine, on_start=None, on_stop=None, on_close=None, **run_kw):
    """event_sim.run on fakes. -> (its return value or the exception it raised, out, stack)."""
    import browser
    scn = scenario.load(scenario.find("smoke", os.path.join(SIM, "scenarios")), REG)
    out = tempfile.mkdtemp(prefix="event-sim-stop-")
    atexit.register(shutil.rmtree, out, True)
    saved = stack.Stack, engine.Engine, browser.Browser
    stack.Stack, engine.Engine, browser.Browser = _StopStack, engine_cls, _ClosingBrowser
    _StopStack.on_start, _StopStack.on_stop, _ClosingBrowser.on_close, _StopStack.last = on_start, on_stop, on_close, None
    try:
        result = event_sim.run(scn, out, **run_kw)
    except KeyboardInterrupt as exc:
        result = exc
    finally:
        stack.Stack, engine.Engine, browser.Browser = saved
        _StopStack.on_start = _StopStack.on_stop = _ClosingBrowser.on_close = None
    return result, out, _StopStack.last


def _report(out):
    with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
        return json.load(fh)


def t_a_stop_during_the_teardown_waits_until_the_report_is_written():
    for where in ("on_stop", "on_close"):
        result, out, st = _fake_run(**{where: lambda: signal.raise_signal(signal.SIGTERM)})
        assert getattr(result, "signum", None) == signal.SIGTERM, f"{where}: the stop still ends the run: {result!r}"
        assert st.stopped and not os.path.exists(st.work), f"{where}: the teardown finishes"
        data = _report(out)
        assert not data["ok"] and any("SIGTERM" in f["message"] for f in data["findings"]), \
            f"{where}: the report names the stop: {data['findings']}"


def t_a_stop_while_the_stack_starts_tears_down_and_writes_a_report():
    result, out, st = _fake_run(on_start=lambda: signal.raise_signal(signal.SIGTERM))
    assert getattr(result, "signum", None) == signal.SIGTERM, result
    assert st.stopped and not os.path.exists(st.work), "the half-started stack is torn down"
    data = _report(out)
    assert not data["ok"] and "SIGTERM" in data["findings"][0]["message"], data["findings"]


class _Unwritable:
    """A stream on a hung-up terminal."""

    def write(self, _text):
        raise OSError(5, "Input/output error")

    def flush(self):
        raise OSError(5, "Input/output error")


def t_a_hung_up_terminal_does_not_change_the_exit_code():
    old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = _Unwritable()
    try:
        result, out, _ = _fake_run()
    finally:
        sys.stdout, sys.stderr = old
    assert result == 0, f"a passing run still exits 0 when its console is gone: {result!r}"
    old_run, old_enter = event_sim.run, event_sim.e2e_safety.enter

    def stopped(*_, **__):
        raise event_sim.Stopped(signal.SIGTERM)
    try:
        event_sim.run, event_sim.e2e_safety.enter = stopped, lambda *a, **kw: None
        sys.stdout = sys.stderr = _Unwritable()
        code = event_sim.main(["--scenario", "smoke", "--out", out])
    finally:
        sys.stdout, sys.stderr = old
        event_sim.run, event_sim.e2e_safety.enter = old_run, old_enter
    assert code == 128 + signal.SIGTERM, f"a stop still ends with 143 when its console is gone, not {code}"


class _BlockingCallEngine:
    """An engine inside a blocking browser call when the stops arrive. Like sync Playwright's
    dispatcher greenlet, the call breaks for good if a signal handler raises inside it."""
    broke = None

    def __init__(self, scn, ctx, *_, interrupted=None, **__):
        self.scenario, self.results, self.start, self.interrupted = scn, {}, None, interrupted

    def run(self):
        try:
            for num in (signal.SIGTERM, signal.SIGINT, signal.SIGTERM):
                signal.raise_signal(num)
        except BaseException as exc:
            _BlockingCallEngine.broke = exc
            raise
        for _ in range(200):
            self.interrupted()
            time.sleep(0.01)
        raise AssertionError("the engine never saw the stop")

    def data(self, results):
        return _QuietEngine.data(self, results)


def t_a_stop_during_a_browser_call_is_raised_from_the_engine_not_inside_the_call():
    _BlockingCallEngine.broke = None
    result, out, st = _fake_run(engine_cls=_BlockingCallEngine)
    assert _BlockingCallEngine.broke is None, \
        f"a signal handler must not raise inside a browser call: {_BlockingCallEngine.broke!r}"
    assert getattr(result, "signum", None) == signal.SIGTERM, f"the first stop ends the run: {result!r}"
    assert st.stopped and not os.path.exists(st.work), "the run is torn down"
    assert "SIGTERM" in _report(out)["findings"][0]["message"], "the report names the stop"


def t_the_engine_sees_a_stop_between_steps_and_inside_its_waits():
    waiting = [{"id": "w", "role": "rec", "do": "go", "expect": ["box.value == 1"], "timeout": "30s"}]
    later = [{"id": "late", "role": "rec", "do": "go", "at": "2m"}]
    for steps in (waiting, later):
        clock, ctx = Clock(), engine.RunContext()
        ctx.box = Box()

        def interrupted(clock=clock):
            if clock() - 1000 >= 5:
                raise event_sim.Stopped(signal.SIGTERM)
        eng = engine.Engine(_scn(steps), ctx, {"box": ctx.box}, registry=_reg(), clock=clock, sleep=clock.sleep,
                            interrupted=interrupted)
        try:
            eng.run()
            raise AssertionError(f"{steps[0]['id']}: the engine ran on after the stop")
        except event_sim.Stopped:
            pass  # raised from the engine's own loop
        assert clock() - 1000 < 6, f"{steps[0]['id']}: the stop is seen within a poll, not after {clock() - 1000:g}s"


def t_main_exits_non_zero_on_a_stop_signal():
    old_run, old_enter = event_sim.run, event_sim.e2e_safety.enter
    out = tempfile.mkdtemp(prefix="event-sim-stop-")
    atexit.register(shutil.rmtree, out, True)

    def stopped(*_, **__):
        raise event_sim.Stopped(signal.SIGTERM)
    try:
        event_sim.run, event_sim.e2e_safety.enter = stopped, lambda *a, **kw: None
        code = event_sim.main(["--scenario", "smoke", "--out", out])
    finally:
        event_sim.run, event_sim.e2e_safety.enter = old_run, old_enter
    assert code == 128 + signal.SIGTERM, f"a SIGTERM ends the run with 143, not {code}"


# -- expect ----------------------------------------------------------------------
def t_expect_reads_probes_views_and_calls():
    box = Box(3, {"Race Control": "GREEN", "rows": [{"URL": "sim://a"}]})
    names = {"box": box}
    assert expect.truth("box.value == 3 and box.twice(2) == 4", names)[0]
    assert expect.truth("box.data.race_control == 'GREEN'", names)[0], "keys match without case"
    assert expect.truth("box.data.rows[0].url == 'sim://a'", names)[0]
    assert expect.truth("len(box.data.rows) == 1 and 'sim://a' in ['sim://a', 'x']", names)[0]
    assert expect.truth("not box.value < 0", names)[0]


def t_expect_refuses_everything_else():
    for text in ("__import__('os')", "box.__class__", "box._secret", "[x for x in box.data]",
                 "lambda: 1", "box.value + 1 == 4", "open('f')", "box.twice(n=2)", "b'x' == b'x'",
                 "x if box else y", "f'{box}'", "page('viewer').fastest.startswith('Fastest')",
                 "box.data.rows.count(1) == 1"):
        try:
            expect.compile_expr(text, ROOTS)
        except expect.ExpectError:
            continue
        raise AssertionError(f"{text!r} must be refused")
    _refused([{"role": "rec", "do": "go", "expect": ["relya.up"]}], "unknown name 'relya'")


def t_expect_compares_rows_of_two_readings():
    before = [{"entry": "A #1", "gap_s": 0.0}, {"entry": "B #2", "gap_s": 4.0}, {"entry": "C #3", "gap_s": None}]
    after = [{"entry": "B #2", "gap_s": 4.7}, {"entry": "A #1", "gap_s": 0.0}, {"entry": "C #3", "gap_s": 9.0}]
    names = {"box": Box(0, {"before": before, "after": after})}
    assert expect.truth("pluck(box.data.after, 'entry') == ['B #2', 'A #1', 'C #3']", names)[0]
    assert expect.truth("row(box.data.after, 'entry', 'B #2').gap_s == 4.7", names)[0]
    assert expect.truth("drift(box.data.before, box.data.after, 'gap_s') < 0.71", names)[0], \
        "the largest change of a key between rows of the same entry, None on either side left out"
    assert not expect.truth("drift(box.data.before, box.data.after, 'gap_s') < 0.69", names)[0]
    names = {"box": Box(0, {"before": before + [{"entry": "D #4", "gap_s": 10.0}],
                            "after": after + [{"entry": "D #4", "gap_s": 8.5}]})}
    assert expect.truth("drift(box.data.before, box.data.after, 'gap_s') == 1.5", names)[0], \
        "a gap that shrank drifted as far as one that grew"
    for text in ("row(box.data.after, 'entry', 'Z #9').gap_s == 1",
                 "drift(box.data.before, [], 'gap_s') < 1", "pluck(box.data.after, 'nope') == []"):
        try:
            expect.truth(text, names)
        except expect.Unresolved:
            continue
        raise AssertionError(f"{text!r} must stay unresolved: nothing to compare is not a pass")
    lost = {"box": Box(0, {"before": before, "after": [{"entry": "A #1", "gap_s": 0.0},
                                                       {"entry": "B #2", "gap_s": None}]})}
    try:
        expect.truth("drift(box.data.before, box.data.after, 'gap_s') <= 1", lost)
    except expect.Unresolved:
        pass  # the expected outcome
    else:
        raise AssertionError("a row that had a number before and none after is no drift of 0")
    expect.compile_expr("drift(result('a').entries, race.entries, 'gap_s') <= 1", ROOTS)


def t_swaps_names_only_pairs_further_apart_than_the_margin():
    before = [{"entry": "A", "gap_s": 0.0}, {"entry": "B", "gap_s": 1.0}, {"entry": "C", "gap_s": 1.02},
              {"entry": "D", "gap_s": None}]
    near = [{"entry": "A"}, {"entry": "C"}, {"entry": "B"}, {"entry": "D"}]
    far = [{"entry": "B"}, {"entry": "A"}, {"entry": "C"}, {"entry": "D"}]
    names = {"box": Box(0, {"before": before, "near": near, "far": far})}
    assert expect.truth("swaps(box.data.before, box.data.near, 'gap_s', 0.2) == []", names)[0], \
        "cars 0.02 s apart may swap"
    assert expect.truth("swaps(box.data.before, box.data.far, 'gap_s', 0.2) == [['A', 'B']]", names)[0], \
        "a car 1 s ahead that fell behind is a swap"
    assert expect.truth("swaps(box.data.before, box.data.near, 'gap_s', 0) == [['B', 'C']]", names)[0]
    assert expect.truth("sorted(pluck(box.data.near, 'entry')) == sorted(pluck(box.data.before, 'entry'))",
                        names)[0], "sorted compares the set of entries"
    try:
        expect.truth("swaps(box.data.before, [], 'gap_s', 0.2) == []", names)
    except expect.Unresolved:
        pass  # the expected outcome
    else:
        raise AssertionError("an empty reading has no order to keep, so it is no pass")


def t_unchanged_names_the_rows_whose_key_kept_its_value():
    before = [{"entry": "A", "driver": "A-1"}, {"entry": "B", "driver": "B-1"}, {"entry": "C", "driver": "C-1"}]
    after = [{"entry": "B", "driver": "B-2"}, {"entry": "A", "driver": "A-2"}, {"entry": "C", "driver": "C-1"}]
    names = {"box": Box(0, {"before": before, "after": after})}
    assert expect.truth("unchanged(box.data.before, box.data.after, 'driver') == ['C']", names)[0]
    assert expect.truth("unchanged(box.data.after, box.data.after, 'driver') == ['B', 'A', 'C']", names)[0]
    try:
        expect.truth("unchanged(box.data.before, [], 'driver') == []", names)
    except expect.Unresolved:
        pass  # the expected outcome
    else:
        raise AssertionError("no common row is nothing compared, not 'every driver changed'")


def t_having_picks_the_texts_that_contain_every_word():
    names = {"box": Box(0, {"rows": ["1 Apex Works 1:44", "7 Falcon Engineering DC", "8 Harbor DC"]})}
    assert expect.truth("having(box.data.rows, 'Falcon', 'DC') == ['7 Falcon Engineering DC']", names)[0]
    assert expect.truth("len(having(box.data.rows, 'DC')) == 2", names)[0]
    assert expect.truth("having(box.data.rows, 'Falcon', '1:44') == []", names)[0]


def t_only_plain_values_leave_a_probe():
    names = {"box": Box(1, {"a": [1, 2]}), "page": expect.Func(lambda r: {"r": r})}
    for text in ("box.twice(box) == 1", "box.twice(page) == 1", "box.twice == 1", "len == 1", "box == 1",
                 "page == 1", "box.twice(box.data) == 1", "box", "box.twice", "[box] == [1]",
                 "box and True", "not box", "box.kind(box) == 'Box'", "box.kind(page) == 'Func'"):
        try:
            expect.truth(text, names)
        except expect.Unresolved:
            continue
        raise AssertionError(f"{text!r} must not hand a probe or a function around")
    assert expect.truth("box.twice(box.data.a) == [1, 2, 1, 2]", names)[0], "plain data may pass"


def t_missing_values_are_unresolved_not_false():
    names = {"box": Box(1, {"a": 1})}
    for text in ("box.data.b == 1", "box.nothing", "box.data.a.b", "box.data[3]", "box.value < 'x'"):
        try:
            expect.truth(text, names)
        except expect.Unresolved:
            continue
        raise AssertionError(f"{text!r} must stay unresolved")


# -- engine ----------------------------------------------------------------------
def _by_id(data):
    return {s["id"]: s for s in data["steps"]}


def t_steps_start_when_their_trigger_fires_not_in_file_order():
    data, clock, _ = _run([{"id": "a", "role": "rec", "do": "go", "at": "10s"},
                           {"id": "b", "role": "rec:x", "do": "go", "at": "after:a+5s"},
                           {"id": "c", "role": "rec", "do": "go", "at": "1s"}])
    started = {k: v["started"] for k, v in _by_id(data).items()}
    assert 1 <= started["c"] < 2, started
    assert 10 <= started["a"] < 11 and started["b"] >= started["a"] + 5, started
    assert [s["id"] for s in data["steps"]] == ["c", "a", "b"], "the timeline is in execution order"
    assert data["ok"] and [n for n, _ in Recorder.log] == ["rec", "rec", "rec:x"]


def t_steps_ready_together_run_in_file_order_one_at_a_time():
    data, _, _ = _run([{"id": "x", "role": "rec", "do": "go", "at": "5s"},
                       {"id": "y", "role": "rec", "do": "go", "at": "5s"},
                       {"id": "z", "role": "rec", "do": "go"}])
    assert [s["id"] for s in data["steps"]] == ["z", "x", "y"], data["steps"]
    s = _by_id(data)
    assert s["y"]["started"] >= s["x"]["started"] + s["x"]["duration"], "one action at a time"


def t_a_waiting_race_trigger_does_not_hold_back_later_steps():
    race = Box(0)
    race.resolve = lambda name: {"lap": expect.View({"leader": race.value}), "state": "racing"}[name]
    clock = Clock()

    def sleep(s):
        clock.sleep(s)
        race.value = int(clock.t - 1000) // 10
    eng = engine.Engine(_scn([{"id": "final", "role": "rec", "do": "go", "at": "lap:leader=3"},
                              {"id": "early", "role": "rec", "do": "go", "at": "10s"},
                              {"id": "chained", "role": "rec", "do": "go", "args": {"v": "${final.id}"}}]),
                        engine.RunContext(), {"race": race}, registry=_reg(), clock=clock, sleep=sleep)
    data = eng.run()
    s = _by_id(data)
    assert 10 <= s["early"]["started"] < 11, s["early"]
    assert s["final"]["started"] >= 30 > s["early"]["started"], s["final"]
    assert s["chained"]["started"] >= s["final"]["started"], "a placeholder waits for its step"
    assert data["ok"], data["findings"]


def t_wait_counts_from_when_the_trigger_is_armed():
    slow = {"id": "a", "role": "rec", "do": "bump", "at": "30s"}
    data, _, _ = _run([slow, {"id": "b", "role": "rec", "do": "go", "at": "after:a", "wait": "20s"}])
    assert _by_id(data)["b"]["status"] == "ok", "an unarmed after: does not count its wait"
    data, _, _ = _run([{"id": "t", "role": "rec", "do": "go", "at": "30s"}], wait="20s")
    assert _by_id(data)["t"]["status"] == "ok", "a time trigger fires at its time"
    data, _, _ = _run([{"id": "a", "role": "rec", "do": "go", "at": "30s"},
                       {"id": "b", "role": "rec", "do": "go", "at": "after:a+40s", "wait": "20s"}])
    b = _by_id(data)["b"]
    assert b["status"] == "ok" and b["started"] >= 70, b
    data, _, _ = _run([{"id": "r", "role": "rec", "do": "go", "at": "lap:leader=9", "wait": "15s"}],
                      race=_race_box())
    r = _by_id(data)["r"]
    assert r["status"] == "failed" and "within 15s of the run start" in r["error"], r


def t_a_trigger_that_never_fires_fails_at_its_own_timeout():
    data, _, _ = _run([{"id": "lap", "role": "rec", "do": "go", "at": "lap:leader=99", "wait": "20s"},
                       {"id": "default", "role": "rec", "do": "go", "at": "state:finished"},
                       {"id": "fine", "role": "rec", "do": "go", "at": "40s"}],
                      max_minutes=2, race=_race_box())
    s = _by_id(data)
    assert s["lap"]["status"] == "failed" and "did not fire" in s["lap"]["error"], s["lap"]
    assert 20 <= s["lap"]["started"] < 21, "it fails at its own wait, not at the end of the run"
    assert s["fine"]["status"] == "ok"
    assert s["default"]["status"] == "failed" and s["default"]["started"] >= 119, \
        "without a wait the trigger may fire until the run ends"
    hard = [f["step"] for f in data["findings"] if f["severity"] == "hard"]
    assert sorted(hard) == ["default", "lap"], data["findings"]


def t_expect_is_retried_until_true_and_slowness_is_soft():
    box = Box(0)
    steps = [{"id": "bump", "role": "rec", "do": "bump", "args": {"to": 0}, "expect": ["box.value == 0"]},
             {"id": "late", "role": "rec", "do": "go", "expect": ["box.value == 1"], "timeout": "10s"}]
    clock = Clock()
    ctx = engine.RunContext()
    ctx.box = box
    real_sleep = clock.sleep

    def sleep(s):
        real_sleep(s)
        if clock.t > 1003:
            box.value = 1
    eng = engine.Engine(_scn(steps), ctx, {"box": box}, registry=_reg(), clock=clock, sleep=sleep)
    data = eng.run()
    late = data["steps"][1]
    assert data["ok"] and late["checks"][0]["ok"], data
    assert late["checks"][0]["elapsed"] > 2
    assert any(f["severity"] == "soft" and "took" in f["message"] for f in data["findings"]), data["findings"]
    assert late["acted"] is not None and late["started"] <= late["acted"] <= late["started"] + 0.5 \
        and late["started"] + late["duration"] - late["acted"] > 2, \
        f"acted is when the action returned, before its expects were awaited: {late}"


def t_a_false_expect_is_a_hard_failure_with_its_last_value():
    data, _, _ = _run([{"role": "rec", "do": "go", "expect": ["box.value == 5"], "timeout": "3s",
                        "soft": ["box.value == 6"]}])
    assert not data["ok"]
    hard = [f for f in data["findings"] if f["severity"] == "hard"]
    soft = [f for f in data["findings"] if f["severity"] == "soft"]
    assert len(hard) == 1 and "left side 0" in hard[0]["message"], hard
    assert len(soft) == 1 and "box.value == 6" in soft[0]["message"], soft
    assert data["findings"][0]["severity"] == "hard", "hard findings come first"


def t_results_feed_later_args_and_a_failed_source_skips_its_users():
    data, _, _ = _run([{"id": "make", "role": "rec", "do": "go"},
                       {"id": "use", "role": "rec", "do": "go",
                        "args": {"id": "${make.id}", "text": "n=${make.nested.k}"}},
                       {"id": "broken", "role": "rec", "do": "fail"},
                       {"id": "after_broken", "role": "rec", "do": "go", "at": "after:broken+1s"},
                       {"id": "needs_broken", "role": "rec", "do": "go", "args": {"x": "${broken.id}"}}])
    assert Recorder.log[1][1] == {"id": 7, "text": "n=v"}, "a whole placeholder keeps the type"
    status = {s["id"]: s["status"] for s in data["steps"]}
    assert status == {"make": "ok", "use": "ok", "broken": "failed", "after_broken": "skipped",
                      "needs_broken": "skipped"}, status
    assert [f["step"] for f in data["findings"] if f["severity"] == "hard"] == ["broken"], \
        "only the root cause is a hard finding"


def _race_box():
    race = Box(0)
    race.resolve = lambda name: {"lap": expect.View({"leader": 0}), "state": "racing"}[name]
    return race


def t_race_triggers_wait_for_their_condition():
    race = Box(0)
    race.resolve = lambda name: {"lap": expect.View({"leader": race.value}), "state": "racing"}[name]
    clock = Clock()

    def sleep(s):
        clock.sleep(s)
        race.value = int(clock.t - 1000) // 10
    ctx = engine.RunContext()
    eng = engine.Engine(_scn([{"role": "rec", "do": "go", "at": "lap:leader=3"},
                              {"id": "never", "role": "rec", "do": "go", "at": "state:finished", "wait": "30s"}]),
                        ctx, {"race": race}, registry=_reg(), clock=clock, sleep=sleep)
    data = eng.run()
    s = _by_id(data)
    assert s["go"]["status"] == "ok" and s["go"]["started"] >= 30, s["go"]
    assert s["never"]["status"] == "failed" and "did not fire" in s["never"]["error"]


def t_a_page_that_raises_keeps_the_check_pending_and_the_run_going():
    class Broken(Recorder):
        kind = "broken"

        def page(self):
            raise RuntimeError("navigation in progress")
    reg = dict(_reg(), broken=Broken)
    clock = Clock()
    scn = scenario.from_dict({"step": [{"role": "broken", "do": "go", "timeout": "2s",
                                        "expect": ["page('broken').x == 1"]},
                                       {"id": "next", "role": "rec", "do": "go"}]}, reg)
    data = engine.Engine(scn, engine.RunContext(), {}, registry=reg, clock=clock, sleep=clock.sleep).run()
    assert [s["status"] for s in data["steps"]] == ["failed", "ok"], data["steps"]
    assert data["steps"][1]["id"] == "next"
    assert "navigation in progress" in data["steps"][0]["checks"][0]["detail"]


def t_the_run_ends_at_max_minutes():
    data, _, _ = _run([{"id": "late", "role": "rec", "do": "go", "at": "2m"},
                       {"id": "slow", "role": "rec", "do": "go", "at": "50s", "expect": ["box.value == 1"],
                        "timeout": "30s"},
                       {"id": "after_slow", "role": "rec", "do": "go", "at": "after:slow"}], max_minutes=1)
    s = _by_id(data)
    assert s["late"]["status"] == "failed" and "past the end of the run" in s["late"]["error"], s["late"]
    assert s["slow"]["status"] == "failed" and s["after_slow"]["status"] == "skipped", data["steps"]
    assert s["slow"]["checks"][0]["elapsed"] <= 10.5, "a check stops at the end of the run"
    assert data["duration"] <= 60 + engine.POLL_S, data["duration"]


def t_a_check_never_outlasts_the_run():
    data, _, _ = _run([{"id": "long", "role": "rec", "do": "go", "expect": ["box.value == 1"], "timeout": "3h"},
                       {"id": "next", "role": "rec", "do": "go", "at": "30s"}], max_minutes=1)
    s = _by_id(data)
    assert data["duration"] <= 60 + engine.POLL_S, data["duration"]
    assert s["long"]["status"] == "failed" and "end of the run" in s["long"]["checks"][0]["detail"], s["long"]
    assert s["next"]["status"] == "failed" and "run ended" in s["next"]["error"], s["next"]


def t_max_minutes_bounds_the_run():
    assert _scn([{"role": "rec", "do": "go"}]).max_minutes == 12
    scn = _scn([{"role": "rec", "do": "go"}], max_minutes=90)
    assert scn.max_minutes == 90 and stack.lifetime(scn) == 90 * 60 + 600
    assert stack.lifetime(_scn([{"role": "rec", "do": "go"}], max_minutes=120)) <= stack.e2e_safety.LONG_MAX_SECONDS
    for bad in (0, 121, "12", 1.5, True):
        _refused([{"role": "rec", "do": "go"}], "max_minutes", max_minutes=bad)
    _refused([{"role": "rec", "do": "go"}], "unknown top-level", timeout="5m")


def t_shots_land_under_their_name_and_a_missing_one_is_soft():
    data, _, tmp = _run([{"id": "a", "role": "rec", "do": "go", "shot": "tower-1"},
                         {"id": "b", "role": "rec", "do": "go", "shot": {"name": "obs-1", "role": "obs"}}])
    assert os.path.isfile(os.path.join(tmp, "shots", "tower-1.png"))
    assert data["steps"][0]["shot"] == "shots/tower-1.png" and data["steps"][1]["shot"] == ""
    assert data["ok"], "a missing screenshot never fails the run"
    assert any(f["severity"] == "soft" and "obs-1" in f["message"] and "no simulated OBS" in f["message"]
               for f in data["findings"]), data["findings"]


def t_role_findings_and_close_and_final_checks():
    closed = []

    class Closing(Recorder):
        kind = "closing"

        def close(self):
            closed.append(self.name)
            return [("hard", "console error: boom")]
    reg = dict(_reg(), closing=Closing)
    clock = Clock()
    scn = scenario.from_dict({"step": [{"role": "closing:a", "do": "note"}, {"role": "rec", "do": "fail"}]}, reg)
    eng = engine.Engine(scn, engine.RunContext(), {}, registry=reg, clock=clock, sleep=clock.sleep,
                        final_checks=[lambda: [("hard", "relay log: traceback")]])
    data = eng.run()
    assert closed == ["closing:a"], "every role closes, also after a failure"
    msgs = [(f["severity"], f["message"]) for f in data["findings"]]
    for want in (("soft", "no visible feedback"), ("hard", "console error: boom"),
                 ("hard", "relay log: traceback"), ("hard", "button missing")):
        assert want in msgs, (want, msgs)


def t_result_probe_reads_an_earlier_step():
    data, _, _ = _run([{"id": "make", "role": "rec", "do": "go"},
                       {"id": "check", "role": "rec", "do": "go",
                        "expect": ["result('make').nested.k == 'v'"], "soft": ["result('later').id == 7"],
                        "timeout": "1s"},
                       {"id": "later", "role": "rec", "do": "go", "at": "1m"}])
    s = _by_id(data)
    assert s["check"]["checks"][0]["ok"] and not s["check"]["checks"][1]["ok"], s["check"]["checks"]
    assert "'later' has no result yet" in s["check"]["checks"][1]["detail"], s["check"]["checks"][1]


def t_page_probe_reads_an_acting_role():
    class Paged(Recorder):
        kind = "paged"

        def page(self):
            return {"tally": "LIVE"}
    reg = dict(_reg(), paged=Paged)
    clock = Clock()
    scn = scenario.from_dict({"step": [{"role": "paged:Comms 1", "do": "go",
                                        "expect": ["page('paged:Comms 1').tally == 'LIVE'"]}]}, reg)
    data = engine.Engine(scn, engine.RunContext(), {}, registry=reg, clock=clock, sleep=clock.sleep).run()
    assert data["ok"], data


# -- report ----------------------------------------------------------------------
def t_report_writes_json_and_escaped_html():
    data, _, tmp = _run([{"id": "x", "role": "rec", "do": "go", "expect": ["box.value == '<b>'"],
                          "timeout": "1s", "shot": "s1"}])
    out = os.path.join(tmp, "out")
    path = report.write(out, data)
    with open(os.path.join(out, "report.json"), encoding="utf-8") as fh:
        assert json.load(fh)["scenario"] == "t"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "&lt;b&gt;" in text and "<b>'" not in text, "expressions are escaped"
    assert "FAIL" in text and "shots/s1.png" in text
    for rule in ("traceback", "ERROR line", "script error", "failed resource load"):
        assert rule in text, f"the legend explains {rule!r}"
    data, _, _ = _run([{"id": "n", "role": "rec", "do": "note"}, {"id": "g", "role": "rec", "do": "go"}])
    rows = {r.split("<small>")[1].split("<")[0]: r for r in report.render_html(data).split("<tr>") if "<small>" in r}
    assert "no visible feedback" in rows["n"] and "no visible feedback" not in rows["g"], \
        "a step's row lists its own findings"


# -- stack helpers ---------------------------------------------------------------
def t_feed_urls_move_to_the_free_ports():
    col = {"sources": [{"settings": {"input": "http://127.0.0.1:53001"}},
                       {"settings": {"input": "http://127.0.0.1:53001/x", "other": "http://example.org:53001"}},
                       {"settings": {"input": "http://127.0.0.1:530011"}}]}
    out = stack.rewrite_feed_urls(col, {53001: 41000})
    s = out["sources"]
    assert s[0]["settings"]["input"] == "http://127.0.0.1:41000"
    assert s[1]["settings"]["input"] == "http://127.0.0.1:41000/x"
    assert s[1]["settings"]["other"] == "http://example.org:53001", "only loopback URLs move"
    assert s[2]["settings"]["input"] == "http://127.0.0.1:530011"
    assert col["sources"][0]["settings"]["input"].endswith(":53001"), "the input is not changed"


def t_free_ports_avoid_the_product_ports():
    offered = iter([8088, 53001, 41000, 8089, 41000, 41001])
    assert stack.free_port(pick=lambda: next(offered)) == 41000
    assert stack.free_port({41000}, pick=lambda: next(offered)) == 41001
    taken = set()
    for _ in range(5):
        taken.add(stack.free_port(taken))
    assert len(taken) == 5 and not taken & stack.RESERVED_PORTS


def t_child_env_drops_inherited_racecast_values():
    env = stack.child_env({"PATH": "/bin", "RACECAST_SHEET_PUSH_URL": "https://real", "RACECAST_OBS_WS_PASSWORD": "x"},
                          {"RACECAST_SIMULATION": "1"})
    assert env == {"PATH": "/bin", "RACECAST_SIMULATION": "1"}, env


def t_stack_pins_auto_arm():
    env = stack.child_env({"RACECAST_MANUAL_FEED_ARM": "1"}, stack.STACK_ENV)
    assert env["RACECAST_MANUAL_FEED_ARM"] == "0", \
        f"the scenarios rely on warm feeds, so no inherited value may switch to manual arm: {env}"


def t_sheet_tabs_follow_the_event():
    scn = scenario.from_dict({"event": {"stints": 3, "commentators": [
        {"name": "Comms 1", "channel": "comms1", "scheduled": True},
        {"name": "Comms 2", "channel": "comms2"}]},
        "sheet": {"tabs": {"POV": [["URL", "Name"], ["sim://pov", "Dan"]]}},
        "step": [{"role": "rec", "do": "go"}]}, _reg())
    tabs = stack.sheet_tabs(scn)
    assert tabs["Schedule"] == [["URL", "Streamer", "Stint"], ["sim://comms1", "Comms 1", "Stint 1"],
                                ["", "Comms 2", "Stint 2"], ["", "", "Stint 3"]], tabs["Schedule"]
    assert [r[2] for r in tabs["Configuration"][1:3]] == ["Comms 1", "Comms 2"]
    assert tabs["POV"][1] == ["sim://pov", "Dan"], "the scenario's own tabs win"


def t_the_schedule_carries_each_stints_lap_count():
    scn = scenario.from_dict({"event": {"stints": 3, "stint_laps": [14, 14, 12]},
                              "step": [{"role": "rec", "do": "go"}]}, _reg())
    assert stack.sheet_tabs(scn)["Schedule"] == [["URL", "Streamer", "Stint", "Laps"], ["", "", "Stint 1", "14"],
                                                 ["", "", "Stint 2", "14"], ["", "", "Stint 3", "12"]]
    for bad in ([14, 14], [14, 0, 12], [14, "14", 12], 14, [14, 14, 1000]):
        _refused([{"role": "rec", "do": "go"}], "event.stint_laps", event={"stints": 3, "stint_laps": bad})


def t_the_stint_vocabulary_holds_every_label_the_schedule_tabs_use():
    quali = [["URL", "Streamer", "Stint"], ["", "Comms 1", "Qualifying"]]
    scn = _scn([{"role": "rec", "do": "go"}], event={"stints": 2, "commentators": [{"name": "Comms 1", "channel": "c1"}]},
               sheet={"tabs": {"Qualifying": quali}})
    config = stack.sheet_tabs(scn)["Configuration"]
    vocab = scenario._relay().parse_config_vocab(scenario._csv(config))["stint"]
    assert vocab == ["Stint 1", "Stint 2", "Qualifying"], \
        f"the relay writes a Schedule or Qualifying label only from the Configuration Stints column: {vocab}"


def t_a_link_step_names_a_stint_of_the_configuration_vocabulary():
    event = {"stints": 2, "commentators": [{"name": "Comms 1", "channel": "c1"}]}
    submit = {"role": "commentator:Comms 1", "do": "submit_link", "args": {"url": "sim://c1", "stint": 2}}
    approve = {"id": "ok", "role": "director", "do": "approve_link", "args": {"stint": "Stint 2"}}
    assert _scn([submit, approve], event=event).steps, "the event's own stints are in the vocabulary"
    _refused([dict(submit, args={"url": "sim://c1", "stint": 3})], "'Stint 3' is not in the Configuration stint",
             event=event)
    _refused([dict(approve, args={"stint": "Qualifying", "area": "schedule"})],
             "'Qualifying' is not in the Configuration stint", event=event)
    quali = {"Qualifying": [["URL", "Streamer", "Stint"], ["", "Comms 1", "Qualifying"]]}
    _scn([dict(approve, args={"stint": "Qualifying", "area": "schedule"})], event=event, sheet={"tabs": quali})


def t_the_report_says_which_track_line_the_run_drove():
    st = stack.Stack(scenario.from_dict({"event": {}, "step": [{"role": "rec", "do": "go"}]}, _reg()), "unused")
    st.track_line = "simulated lap (1200 m)"
    assert event_sim.setup_meta("cpu:1", "off", st) == {"load": "cpu:1", "trace": "off",
                                                       "track_line": "simulated lap (1200 m)"}, \
        "the report keeps which line the cars drove, so a run without the host's line is obvious"


def t_make_home_copies_into_the_temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "s")
        os.makedirs(os.path.join(src, "__pycache__"))
        with open(os.path.join(src, "racecast.py"), "w", encoding="utf-8") as fh:
            fh.write("")
        prof = os.path.join(tmp, "p")
        os.makedirs(prof)
        with open(os.path.join(prof, "profile.env"), "w", encoding="utf-8") as fh:
            fh.write("NAME=x\n")
        home = os.path.join(tmp, "home")
        entry = stack.make_home(home, src, prof)
        assert entry == os.path.join(home, "src", "racecast.py")
        assert os.path.isfile(os.path.join(home, "profiles", "demo", "profile.env"))
        assert os.path.isfile(os.path.join(home, "runtime", "yt-cookies.txt"))
        assert not os.path.exists(os.path.join(home, "src", "__pycache__"))


def t_the_relay_browser_sources_move_with_the_relay():
    col = {"sources": [{"settings": {"url": "http://127.0.0.1:8088/hud"}},
                       {"settings": {"url": "http://127.0.0.1:8088/splitscreen?x=1"}}]}
    out = stack.rewrite_feed_urls(col, stack.port_map((41001, 41002, 41003), 41000))
    assert [s["settings"]["url"] for s in out["sources"]] == ["http://127.0.0.1:41000/hud",
                                                              "http://127.0.0.1:41000/splitscreen?x=1"]
    with open(stack.COLLECTION, encoding="utf-8") as fh:
        real = json.dumps(stack.rewrite_feed_urls(json.load(fh), stack.port_map((41001, 41002, 41003), 41000)))
    for old in ("127.0.0.1:8088", "127.0.0.1:53001", "127.0.0.1:53002", "127.0.0.1:53003"):
        assert old not in real, old


def t_the_control_center_sees_the_engine_relay():
    with tempfile.TemporaryDirectory() as home:
        stack.stamp_relay(home, 4242)
        rt = os.path.join(home, "runtime")
        with open(os.path.join(rt, "relay.pid"), encoding="utf-8") as fh:
            assert fh.read().strip() == "4242"
        with open(os.path.join(rt, "relay.mode"), encoding="utf-8") as fh:
            assert fh.read() == "simulation"
        with open(os.path.join(rt, "relay.profile"), encoding="utf-8") as fh:
            assert fh.read() == "demo"
        stack.stamp_relay(home, os.getpid())
        mark = stack.simulation.process_started(os.getpid())
        if mark:
            with open(os.path.join(rt, "relay.started"), encoding="utf-8") as fh:
                assert fh.read() == mark, "the start marker lets the CLI tell the relay from a recycled PID"


def t_tracebacks_in_a_log_are_hard():
    text = "INFO up\nTraceback (most recent call last):\n  File \"x\", line 1\nValueError: bad\nINFO on\n"
    assert stack.log_findings(text, "relay") == [("hard", "relay log: traceback ValueError: bad")]
    errors = ("2026-11-22 14:25:43 ERROR [streamlink] gone\n"
              "2026-11-22 14:25:53 ERROR [streamlink] gone\n"
              "2026-11-22 14:26:00 ERROR other\n2026-11-22 14:26:00 INFO fine\n")
    assert stack.log_findings(errors, "relay") == [("soft", "relay log: ERROR [streamlink] gone (2x)"),
                                                   ("soft", "relay log: ERROR other")]


# -- probes and built-in roles against in-process fakes --------------------------
def t_sheet_and_webhook_probes_read_the_fakes():
    sheet = sheet_sim.SheetSim(tabs={"Schedule": [["URL", "Streamer", "Stint"], ["sim://a", "Comms 1", "Stint 1"]]}).start()
    sink = webhook_sink.WebhookSink().start()
    try:
        probes.http_json(sink.http_url, "/api/webhooks/1/sim", "POST", {"content": "Stream link approved"})
        names = {"sheet": probes.SheetProbe(sheet), "webhook": probes.WebhookProbe(sink)}
        assert expect.truth("sheet.schedule[0].url == 'sim://a'", names)[0]
        assert expect.truth("sheet.tab('schedule')[0].streamer == 'Comms 1'", names)[0]
        assert expect.truth("sheet.write_count('schedule') == 0", names)[0]
        assert expect.truth("webhook.count('link approved') == 1 and webhook.rejected == 0", names)[0]
        try:
            expect.truth("sheet.nosuchtab", names)
            raise AssertionError("an unknown tab is unresolved")
        except expect.Unresolved:
            pass  # expected
    finally:
        sink.stop()
        sheet.stop()


class _Api(http.server.BaseHTTPRequestHandler):
    seen = []

    def _answer(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Api.seen.append((self.command, self.path, body))
        code = 500 if "boom" in self.path else 200
        data = json.dumps({"ok": code == 200, "id": "s1"}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass  # quiet test output


def t_api_role_adds_tokens_and_fails_on_http_errors():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    th = threading.Thread(target=srv.serve_forever)
    th.start()
    try:
        ctx = engine.RunContext(urls={"relay": f"http://127.0.0.1:{srv.server_address[1]}"},
                                token=lambda name: f"tok-{name.replace(' ', '')}")
        role = builtin.ApiRole(ctx, "api")
        assert role.do("post", {"path": "/cockpit/submit", "as": "Comms 2", "json": {"a": 1}})["id"] == "s1"
        method, path, body = _Api.seen[-1]
        assert (method, path, json.loads(body)) == ("POST", "/cockpit/submit?t=tok-Comms2", {"a": 1})
        for args in ({"path": "/boom"}, {"path": "/x", "service": "nope"}, {"path": "no-slash"}):
            try:
                role.do("get", args)
                raise AssertionError(f"{args} must fail")
            except roles.StepError:
                pass  # expected
    finally:
        srv.shutdown()
        srv.server_close()
        th.join()


class _Proc:
    def poll(self):
        return None


def t_a_relay_crash_kills_only_the_relay_its_pid_file_names():
    scn = _scn([{"role": "rec", "do": "go"}])
    with tempfile.TemporaryDirectory() as work:
        st = stack.Stack(scn, work)
        st.home, st.urls = work, {"relay": "http://127.0.0.1:41000"}
        child = stack.Child.__new__(stack.Child)
        child.name, child.proc, child.expected_exit = "relay", _Proc(), False
        st.children = [child]
        killed = []
        st.home = None
        try:
            st.kill_relay(pids=lambda port: [4242], kill=lambda pid, sig: killed.append(pid))
            raise AssertionError("a stack without its app home reads no relay.pid")
        except RuntimeError as exc:
            assert "app home" in str(exc), exc
        st.home = work
        try:
            st.kill_relay(pids=lambda port: [4242], kill=lambda pid, sig: killed.append(pid))
            raise AssertionError("no PID file is no relay to kill")
        except RuntimeError as exc:
            assert "relay.pid" in str(exc), exc
        stack.stamp_relay(work, 4242)
        try:
            st.kill_relay(pids=lambda port: [4242, 4343], kill=lambda pid, sig: killed.append(pid))
            raise AssertionError("a port held by another process too is not killed")
        except RuntimeError as exc:
            assert "41000" in str(exc) and "4343" in str(exc), exc
        assert not killed, "nothing is killed on a mismatch"
        seen = []
        pid = st.kill_relay(pids=lambda port: seen.append(port) or [4242],
                            kill=lambda pid, sig: killed.append((pid, sig)))
        assert pid == 4242, pid
        assert seen == [41000] and killed == [(4242, stack.KILL_SIGNAL)]
        assert child.expected_exit, "the stack's own relay child may now end without a problem"
    ctx = engine.RunContext(kill_relay=lambda: 4242)
    assert builtin.RelayRole(ctx, "relay").do("kill", {}) == {"pid": 4242}
    try:
        builtin.RelayRole(engine.RunContext(), "relay").do("kill", {})
        raise AssertionError("a run that cannot kill its relay fails the step")
    except roles.StepError:
        pass  # expected


def t_relay_probe_names_its_port():
    assert expect.truth("relay.port == 41234", {"relay": probes.RelayProbe("http://127.0.0.1:41234")})[0]


CONTROLS = ("/next", "/feed/A/activate", "/setup/set/stint/data", "/status?x=/data", "/hud/data/../next",
            "/timer/start", "status", "")


def t_relay_get_never_sends_a_control():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    th = threading.Thread(target=srv.serve_forever)
    th.start()
    try:
        _Api.seen = []
        probe = probes.RelayProbe(f"http://127.0.0.1:{srv.server_address[1]}")
        for path in CONTROLS:
            try:
                probe.get(path)
                raise AssertionError(f"relay.get({path!r}) must be refused")
            except expect.Unresolved:
                pass  # expected
        assert _Api.seen == [], f"a refused path reached the relay: {_Api.seen}"
        for path in ("/status", "/setup/data", "/drivers/race/recording"):
            assert probe.get(path)["ok"] is True
        assert [p for _, p, _ in _Api.seen] == ["/status", "/setup/data", "/drivers/race/recording"]
    finally:
        srv.shutdown()
        srv.server_close()
        th.join()


def t_scenario_refuses_a_relay_get_of_a_control():
    go = {"role": "rec", "do": "go"}
    for path in CONTROLS:
        _refused([dict(go, expect=[f"relay.get({path!r}).ok"])], "relay.get")
    _refused([dict(go, soft=["relay.get('/next')"])], "relay.get")
    _refused([dict(go, expect=["relay.get(result('go').path)"])], "relay.get")
    _refused([dict(go, expect=["relay.get()"])], "relay.get")
    _scn([dict(go, expect=["relay.get('/setup/data').stint == 'Stint 1'", "relay.get('/drivers/race').state"])])


def t_http_json_refuses_non_loopback_hosts():
    for base in ("http://example.org:80", "https://127.0.0.1:1", "http://127.0.0.1.example.org:80"):
        try:
            probes.http_json(base, "/")
            raise AssertionError(base)
        except ValueError:
            pass  # expected


class _FakePage:
    def __init__(self):
        self.handlers, self.url, self.shots = {}, "", []

    def on(self, event, fn):
        self.handlers[event] = fn

    status = 200

    def goto(self, url, timeout=0):
        self.url = url
        page = self

        class R:
            status = page.status
        return R()

    def title(self):
        return "Panel"

    metrics = {"JSHeapUsedSize": 52428800, "Nodes": 1234, "JSEventListeners": 56}
    enable_fails = 0

    def locator(self, selector):
        return selector

    def screenshot(self, path, mask=(), timeout=None):
        if os.path.basename(path) in self.fail_shots:
            raise TimeoutError(f"Timeout {timeout}ms exceeded.")
        self.shots.append(path)
        self.masks = list(mask)

    fail_shots = ()
    hung = False
    state = {"visibility": "visible", "focus": True, "ready": "complete", "title": "Panel"}

    long_tasks = ()

    def wait_for_function(self, script, arg=None, timeout=None):
        if self.hung:
            raise TimeoutError(f"Timeout {timeout}ms exceeded.")
        state = self.state
        if script == builtin.LONG_TASKS:
            state = {"tasks": [t for t in self.long_tasks if t["ago_s"] * 1000 <= arg]}

        class Handle:
            def json_value(self):
                return dict(state)
        return Handle()


class _Tracing:
    def __init__(self):
        self.calls = []

    def start(self, screenshots=False, snapshots=False):
        self.calls.append(("start", screenshots, snapshots))

    def start_chunk(self):
        self.calls.append(("chunk",))

    def stop_chunk(self, path=None):
        self.calls.append(("stop_chunk", os.path.basename(path) if path else None))
        if path:
            with open(path, "wb") as fh:
                fh.write(b"PK")

    def stop(self):
        self.calls.append(("stop",))


class _FakeBrowser:
    problem = ""

    def __init__(self):
        self.page = _FakePage()
        self.tracing = _Tracing()
        self.init_scripts = []

    def get(self):
        return self

    def new_browser_cdp_session(self):
        class Session:
            def send(self, method):
                assert method == "SystemInfo.getProcessInfo", method
                return {"processInfo": [{"type": "renderer", "id": 7, "cpuTime": 12.5}]}

            def detach(self):
                pass  # nothing to release
        return Session()

    def new_context(self, viewport=None):
        outer = self
        self.viewport = viewport

        class C:
            tracing = outer.tracing
            browser = outer

            def new_page(self):
                return outer.page

            def add_init_script(self, script):
                outer.init_scripts.append(script)

            def new_cdp_session(self, page):
                class Session:
                    enabled = False

                    def send(self, method):
                        if page.metrics is None:
                            raise RuntimeError("Target page, context or browser has been closed")
                        if method == "Performance.enable":
                            if page.enable_fails:
                                page.enable_fails -= 1
                                raise RuntimeError("Performance.enable: not ready")
                            self.enabled = True
                        if method == "Performance.getMetrics":
                            if not self.enabled:
                                raise RuntimeError("Performance.getMetrics before Performance.enable")
                            return {"metrics": [{"name": k, "value": v} for k, v in page.metrics.items()]}
                        return {}
                return Session()

            def close(self):
                pass  # nothing to release
        return C()


def t_page_role_opens_shoots_and_reports_console_errors():
    br = _FakeBrowser()
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, token=lambda n: "tok", browser=br)
    role = builtin.PageRole(ctx, "page:director")
    role.open()
    assert role.do("open", {"path": "/panel", "as": "Sim Director"})["title"] == "Panel"
    assert br.page.url == "http://127.0.0.1:9/panel?t=tok"

    class Msg:
        def __init__(self, text):
            self.type, self.text, self.location = "error", text, {"url": "http://127.0.0.1:9/x"}
    br.page.handlers["console"](Msg("Failed to load resource: 503"))
    br.page.handlers["pageerror"]("TypeError: x is undefined")
    role.shot("p.png")
    assert br.page.shots == ["p.png"]
    br.page.status = 404
    try:
        role.do("open", {"path": "/missing", "as": "Sim Director"})
        raise AssertionError("an HTTP error page fails the step")
    except roles.StepError as exc:
        assert "404" in str(exc) and "tok" not in str(exc), str(exc)
    findings = role.close()
    assert sorted(s for s, _ in findings) == ["hard", "soft"], "a script error is hard, a failed load soft"


def t_page_role_reports_its_memory():
    br = _FakeBrowser()
    role = builtin.PageRole(engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br), "page:director")
    role.open()
    assert "heap_mb" not in role.page(), "a page that is not open reports no memory"
    role.do("open", {"path": "/panel"})
    state = role.page()
    assert (state["heap_mb"], state["dom_nodes"], state["listeners"]) == (50.0, 1234, 56), state
    br.page.metrics = {"Nodes": 10}
    assert role.page()["heap_mb"] is None, "a metric the browser does not send stays None"
    br.page.metrics = None
    state = role.page()
    assert state["heap_mb"] is None and state["dom_nodes"] is None, "a crashed page reports no memory"


def t_page_role_enables_the_metrics_again_after_a_failure():
    br = _FakeBrowser()
    role = builtin.PageRole(engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br), "page:director")
    role.open()
    role.do("open", {"path": "/panel"})
    br.page.enable_fails = 1
    assert role.page()["heap_mb"] is None, "no metrics while Performance.enable fails"
    assert role.page()["heap_mb"] == 50.0, "a failed Performance.enable is tried again on the next read"


def _page_run(steps):
    br = _FakeBrowser()
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br)
    tmp = tempfile.mkdtemp(prefix="event-sim-test-")
    atexit.register(shutil.rmtree, tmp, True)
    clock = Clock()
    data = engine.Engine(_scn(steps), ctx, {}, registry=_reg(), clock=clock, sleep=clock.sleep,
                         shots_dir=os.path.join(tmp, "shots")).run()
    return data, br, tmp


def t_the_report_keeps_each_pages_last_memory():
    data, _, _ = _page_run([{"id": "panel", "role": "page:director", "do": "open", "args": {"path": "/panel"}},
                            {"id": "go", "role": "rec", "do": "go"}])
    assert data["pages"] == {"page:director": {"heap_mb": 50.0, "dom_nodes": 1234, "listeners": 56}}, data["pages"]
    page = report.render_html(data)
    assert "page:director" in page and "50.0 MB" in page and "1234 DOM nodes" in page, page


def t_a_failed_page_step_leaves_a_screenshot_named_after_it():
    data, br, _ = _page_run([{"id": "panel", "role": "page:director", "do": "open", "args": {"path": "/panel"}},
                             {"id": "gone", "role": "page:director", "do": "open", "args": {"path": "/x"},
                              "at": "after:panel", "expect": ["box.value == 1"], "timeout": "1s"},
                             {"id": "bad", "role": "page:director", "do": "open", "args": {"path": "x"}},
                             {"id": "go", "role": "rec", "do": "go"}])
    steps = {s["id"]: s for s in data["steps"]}
    assert steps["gone"]["status"] == "failed" and steps["bad"]["status"] == "failed", data["steps"]
    shots = [os.path.basename(p) for p in br.page.shots]
    assert shots == ["fail-gone.png", "fail-bad.png"], f"a failed check and a failed action each leave one: {shots}"
    assert steps["gone"]["fail_shot"] == "shots/fail-gone.png", steps["gone"]
    assert steps["panel"]["fail_shot"] == "" and steps["go"]["fail_shot"] == "", "only a failed step leaves one"
    assert "fail-gone.png" in report.render_html(data)


def t_page_role_opens_in_the_window_size_it_is_given():
    br = _FakeBrowser()
    role = builtin.PageRole(engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br), "page:overlay")
    role.open()
    role.do("open", {"path": "/drivers", "viewport": [1920, 1080]})
    assert br.viewport == {"width": 1920, "height": 1080}, "an OBS page is shot at the canvas size"
    for bad in ([1920], [0, 1080], ["1920", 1080], "1920x1080"):
        other = builtin.PageRole(engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br), "page:x")
        other.open()
        try:
            other.do("open", {"path": "/drivers", "viewport": bad})
            raise AssertionError(f"viewport {bad!r} must be refused")
        except roles.StepError as exc:
            assert "viewport" in str(exc), exc


def t_page_role_keeps_tokens_out_of_what_it_reports():
    br = _FakeBrowser()
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9", "far": "http://example.org"},
                            token=lambda n: "mintedtoken", browser=br)
    role = builtin.PageRole(ctx, "page:commentator")
    role.open()
    opened = role.do("open", {"path": "/cockpit?x=1", "as": "Comms 1"})
    assert br.page.url.endswith("&t=mintedtoken"), "the page itself gets the token"

    class Msg:
        type, text, location = "error", "boom", {"url": "http://127.0.0.1:9/cockpit?t=mintedtoken"}
    br.page.handlers["console"](Msg())
    br.page.handlers["pageerror"]("Error at /cockpit?t=mintedtoken&x=1")
    reported = json.dumps([opened, role.page(), role.close()])
    assert "mintedtoken" not in reported and "REDACTED" in reported, reported
    try:
        role.do("open", {"service": "far", "path": "/"})
        raise AssertionError("pages open on loopback only")
    except roles.StepError:
        pass  # expected


def t_page_role_keeps_to_its_service():
    br = _FakeBrowser()
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, token=lambda n: "tok", browser=br)
    role = builtin.PageRole(ctx, "page")
    role.open()
    for bad in ("x", "@example.org/", "//example.org/", "/\\example.org", "/a\nb", ""):
        try:
            role.do("open", {"path": bad})
        except roles.StepError:
            continue
        raise AssertionError(f"path {bad!r} must be refused (went to {br.page.url})")
    role.do("open", {"path": "/panel?x=@y"})
    assert br.page.url == "http://127.0.0.1:9/panel?x=@y"


def t_page_role_without_a_browser_is_a_skip():
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9"})
    role = builtin.PageRole(ctx, "page")
    role.open()
    try:
        role.do("open", {"path": "/"})
        raise AssertionError("without a browser the step is skipped, which --no-skips fails")
    except roles.Unavailable as exc:
        assert "browser" in str(exc)
    try:
        role.shot("x.png")
        raise AssertionError("no page, no shot")
    except roles.ShotUnavailable:
        pass  # expected


class _Paged(builtin.PageRole):
    """A page role whose `slow` action clicks and waits on the engine's fake clock."""
    kind = "paged"

    def act_slow(self, args):
        with self.timed("request", "NEXT"):
            with self.timed("click", "NEXT"):
                self.clock.t += args.get("click", 0)
            self.clock.t += args.get("answer", 0)
        with self.timed("wait", "the next stint on air", budget=None):
            with self.timed("control", "polled inside a wait"):
                self.clock.t += 9
        return {}


def _diag_run(steps, trace="off", host=None, page=None):
    br = _FakeBrowser()
    if page:
        for k, v in page.items():
            setattr(br.page, k, v)
    clock = Clock()
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br, trace=trace, clock=clock)
    tmp = tempfile.mkdtemp(prefix="event-sim-test-")
    atexit.register(shutil.rmtree, tmp, True)
    reg = dict(_reg(), paged=_Paged)
    eng = engine.Engine(scenario.from_dict({"step": steps}, reg), ctx, {}, registry=reg, clock=clock,
                        sleep=clock.sleep, shots_dir=os.path.join(tmp, "shots"),
                        traces_dir=os.path.join(tmp, "traces"), host=host or (lambda: {"t": clock()}),
                        processes=lambda: [{"pid": 7, "name": "chrome-headless-shell gpu-process", "cpu_s": clock()}])
    _Paged.clock = clock
    return eng.run(), br, tmp


def t_every_page_action_and_shot_is_timed_and_a_slow_one_is_soft():
    data, _, _ = _diag_run([{"id": "open", "role": "paged", "do": "open", "args": {"path": "/panel"}},
                            {"id": "fast", "role": "paged", "do": "slow", "at": "after:open",
                             "args": {"click": 0.5, "answer": 0.5}, "shot": "fast"},
                            {"id": "slowclick", "role": "paged", "do": "slow", "at": "after:fast",
                             "args": {"click": 5, "answer": 0.5}},
                            {"id": "slowanswer", "role": "paged", "do": "slow", "at": "after:slowclick",
                             "args": {"click": 0.1, "answer": 4}},
                            {"id": "obs", "role": "rec", "do": "go", "at": "after:slowanswer", "shot": "rec-1"}])
    s = _by_id(data)
    fast = [(a["role"], a["kind"], a["what"], a["s"], a["depth"]) for a in s["fast"]["actions"]]
    assert fast == [("paged", "request", "NEXT", 1.0, 0), ("paged", "click", "NEXT", 0.5, 1),
                    ("paged", "wait", "the next stint on air", 9.0, 0), ("paged", "shot", "fast", 0.0, 0)], \
        f"each action with its nesting, polls inside a wait are not actions: {fast}"
    assert [a["kind"] for a in s["open"]["actions"]] == ["open"], s["open"]["actions"]
    assert s["obs"]["actions"][0]["kind"] == "shot" and s["obs"]["actions"][0]["role"] == "rec", \
        "a role without a page has its shot timed too"
    slow = [(f["step"], f["message"]) for f in data["findings"] if "took" in f["message"]]
    assert len(slow) == 2 and slow[0][0] == "slowclick" and "click 'NEXT' took 5.0s" in slow[0][1], \
        f"only the slow click, not the request around it: {slow}"
    assert slow[1][0] == "slowanswer" and "request 'NEXT' took 4.1s" in slow[1][1], slow
    assert data["ok"], "slowness is a finding, never a failure"
    shot = s["fast"]["actions"][-1]
    assert "host" in shot and shot["cpu"][0]["name"] == "chrome-headless-shell gpu-process", \
        f"a shot has the host and each process's CPU over its own seconds: {shot}"
    assert [a.get("slow", False) for a in s["slowclick"]["actions"]][:2] == [False, True], s["slowclick"]
    page = report.render_html(data)
    assert "click NEXT" in page and "5.00s" in page, "the report lists each step's actions"


def t_each_step_records_what_the_unit_got_from_the_host():
    samples = iter([{"t": 0.0, "loadavg": [1, 1, 1], "unit": {"cpu": {"usage_usec": 0, "throttled_usec": 0}}},
                    {"t": 2.0, "loadavg": [1, 1, 1], "unit": {"cpu": {"usage_usec": 500_000, "throttled_usec": 0}}}])
    data, _, _ = _diag_run([{"id": "a", "role": "rec", "do": "go"}], host=lambda: next(samples))
    assert data["steps"][0]["host"]["cpu_s"] == 0.5 and data["steps"][0]["host"]["seconds"] == 2.0, data["steps"][0]


def t_a_failed_shot_on_another_page_leaves_a_snapshot_of_that_page():
    data, br, tmp = _diag_run([{"id": "open", "role": "page:rc", "do": "open", "args": {"path": "/rc?t=abc"}},
                               {"id": "to_box", "role": "rec", "do": "go", "at": "after:open",
                                "shot": {"name": "restart-lobby", "role": "page:rc"}}],
                              page={"fail_shots": ("restart-lobby.png",)})
    s = _by_id(data)["to_box"]
    assert any("shot restart-lobby" in f["message"] and "Timeout" in f["message"] for f in data["findings"])
    assert len(s["snapshots"]) == 1, f"the shot's page was diagnosed although the step's role has no page: {s}"
    snap = s["snapshots"][0]
    assert snap["role"] == "page:rc" and "restart-lobby" in snap["why"], snap
    assert snap["processes"][0]["name"] == "chrome-headless-shell gpu-process", "which process of the unit ate the CPU"
    assert data["unit_processes"][0]["cpu_s"] >= snap["processes"][0]["cpu_s"], "and once more at the end of the run"
    assert "gpu-process" in report.render_html(data)
    page = snap["page"]
    assert page["alive"] and page["visibility"] == "visible" and page["metrics"]["Nodes"] == 1234, page
    assert page["processes"] == [{"type": "renderer", "id": 7, "cpu_s": 12.5}], page
    assert "abc" not in json.dumps(snap), "the page URL is scrubbed"
    assert snap["picture"] == "shots/fail-to_box-shot.png" and s["fail_shot"] == snap["picture"], snap
    assert os.path.basename(br.page.shots[-1]) == "fail-to_box-shot.png", br.page.shots
    assert "restart-lobby" in report.render_html(data) and "Failure snapshots" in report.render_html(data)


def t_a_hung_page_is_diagnosed_without_waiting_on_it():
    data, _, _ = _diag_run([{"id": "open", "role": "page:rc", "do": "open", "args": {"path": "/rc"}},
                            {"id": "bad", "role": "page:rc", "do": "open", "args": {"path": "x"}, "at": "after:open"}],
                           page={"hung": True})
    snap = _by_id(data)["bad"]["snapshots"][0]
    assert snap["page"]["alive"] is False and "Timeout" in snap["page"]["error"], snap["page"]
    assert "metrics" not in snap["page"], "a page that does not answer is not asked for metrics"
    assert snap["host"] is not None and "since_step" in snap, snap


def t_the_page_keeps_its_last_console_lines_and_failed_requests():
    br = _FakeBrowser()
    clock = Clock()
    role = builtin.PageRole(engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br), "page:rc")
    role.open()
    role.clock = clock
    role.do("open", {"path": "/rc"})

    class Msg:
        def __init__(self, i):
            self.type, self.text, self.location = "log", f"line {i} /x?t=secret", {}
    for i in range(builtin.RECENT + 5):
        br.page.handlers["console"](Msg(i))

    class Req:
        url, method, failure = "http://127.0.0.1:9/status?t=secret", "GET", "net::ERR_ABORTED"

    class Resp:
        url, status = "http://127.0.0.1:9/next", 503
    for _ in range(builtin.RECENT):
        br.page.handlers["requestfailed"](Req())
    br.page.handlers["response"](Resp())
    clock.t += 3
    d = role.diagnose()
    assert len(d["console"]) == builtin.RECENT and d["console"][-1]["text"].startswith(f"line {builtin.RECENT + 4}")
    assert d["console"][-1]["age_s"] == 3.0, d["console"][-1]
    assert len(d["network"]) == builtin.RECENT and d["network"][-1]["status"] == 503, d["network"]
    assert d["network"][0]["failure"] == "net::ERR_ABORTED", d["network"][0]
    assert "secret" not in json.dumps(d), d


def t_a_slow_or_failed_step_names_the_long_tasks_of_each_page():
    tasks = ({"ago_s": 1.0, "ms": 1900}, {"ago_s": 60.0, "ms": 300})
    data, br, _ = _diag_run([{"id": "open", "role": "page:rc", "do": "open", "args": {"path": "/rc"}},
                             {"id": "quick", "role": "rec", "do": "go", "at": "after:open"},
                             {"id": "slow", "role": "paged", "do": "slow", "args": {"click": 3}, "at": "after:quick"}],
                            page={"long_tasks": tasks})
    assert br.init_scripts == [builtin.LONG_TASK_OBSERVER], "each page records its long tasks from the start"
    s = _by_id(data)
    assert s["quick"]["long_tasks"] == {}, "a quick step asks no page"
    assert s["slow"]["cpu"] and s["quick"]["cpu"] is None, "a slow step names each process's CPU over the step"
    assert s["slow"]["long_tasks"] == {"page:rc": [{"ago_s": 1.0, "ms": 1900}]}, \
        f"only the tasks inside the step: {s['slow']['long_tasks']}"
    assert "1900" in report.render_html(data), "the report shows them under the step"


def t_an_oom_kill_in_the_unit_is_its_own_hard_finding():
    def host(kills):
        return {"t": 0.0, "loadavg": [1, 1, 1], "unit": {"memory_peak": 2 * 1024 ** 3,
                                                         "memory_events": {"oom_kill": kills, "max": kills * 9}}}
    samples = iter([host(0), host(0), host(0), host(1)])
    data, _, _ = _diag_run([{"id": "a", "role": "rec", "do": "go"}, {"id": "b", "role": "rec", "do": "go"}],
                           host=lambda: next(samples))
    hard = [(f["step"], f["message"]) for f in data["findings"] if f["severity"] == "hard"]
    assert len(hard) == 1 and hard[0][0] == "b" and "OOM-killed 1 process" in hard[0][1], hard
    assert "this run's memory cgroup" in hard[0][1] and "unit" not in hard[0][1], \
        "a hosted runner has no E2E unit: the text names the run's cgroup"
    assert "2048 MB" in hard[0][1], "the finding names the unit's memory peak"
    assert not data["ok"], "a killed process is a failure of the run, whatever step it hits next"


class _Host:
    """A host sampler on the fake clock. `unit_io` and `host_io` are the stalled shares
    while time passes; `stall(seconds)` adds stall at once; with `capped` every reading
    counts one more memory.max event of the unit."""

    def __init__(self, clock, unit_io=0.0, host_io=None, capped=False):
        self.clock, self.unit_rate, self.host_rate = clock, unit_io, unit_io if host_io is None else host_io
        self.unit, self.host, self.cap, self.last, self.capped = 0.0, 0.0, 0, clock(), capped

    def _advance(self):
        t = self.clock()
        self.unit += (t - self.last) * self.unit_rate
        self.host += (t - self.last) * self.host_rate
        self.last = t

    def stall(self, seconds):
        self._advance()
        self.unit += seconds
        self.host += seconds

    def __call__(self):
        self._advance()
        self.cap += self.capped
        some = lambda v: {"some": {"total": int(v * 1e6)}}      # noqa: E731
        return {"t": self.clock(), "loadavg": [1, 1, 1], "swap": {"in": 0, "out": 0},
                "pressure": {"io": some(self.host), "memory": some(0), "cpu": some(0)},
                "unit": {"cpu": {"usage_usec": 0, "throttled_usec": 0}, "memory_events": {"max": self.cap, "high": 0},
                         "pressure": {"io": some(self.unit), "memory": some(0), "cpu": some(0)}}}


class _Noisy(Recorder):
    """A role whose page logs a script error while it acts."""
    kind = "noisy"

    def act_go(self, args):
        self.ctx.finding("hard", "console error: TypeError: x is undefined")
        return {}


def _starved_run(steps, host_of, page=None):
    clock = Clock()
    br = _FakeBrowser()
    for k, v in (page or {}).items():
        setattr(br.page, k, v)
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br, clock=clock)
    tmp = tempfile.mkdtemp(prefix="event-sim-test-")
    atexit.register(shutil.rmtree, tmp, True)
    reg = dict(_reg(), paged=_Paged, noisy=_Noisy)
    _Paged.clock = clock
    host = host_of(clock)
    data = engine.Engine(scenario.from_dict({"step": steps}, reg), ctx, {}, registry=reg, clock=clock,
                         sleep=clock.sleep, shots_dir=os.path.join(tmp, "shots"), host=host,
                         processes=lambda: None, no_skips=True).run()
    return data, host


def _causes(data, step):
    return [(f["message"].split(":")[0], f["cause"]) for f in data["findings"]
            if f["severity"] == "hard" and f["step"] == step]


def t_a_failure_while_the_host_starved_the_unit_says_so_and_stays_hard():
    steps = [{"id": "early", "role": "rec", "do": "fail"},
             {"id": "check", "role": "noisy", "do": "go", "expect": ["relay.up"], "timeout": "4s"}]
    data, _ = _starved_run(steps, lambda c: _Host(c, unit_io=0.5))
    assert _causes(data, "early") == [("button missing", "")], \
        f"an earlier step's failure keeps its own class: {_causes(data, 'early')}"
    assert _causes(data, "check") == [("expect 'relay.up' still false after 4s", engine.STARVED),
                                      ("console error", "")], \
        f"the failed check gets the class, the page's script error does not: {_causes(data, 'check')}"
    msg = next(f["message"] for f in data["findings"] if f["cause"])
    assert "host starved: the unit waited" in msg and "host" in msg.split("starved:")[1], msg
    assert not data["ok"], "a starved failure is still a failure"
    for host_of, why in ((lambda c: _Host(c, unit_io=engine.STARVED_SHARE / 2), "too little stall"),
                         (lambda c: _Host(c, unit_io=0.5, host_io=0.0), "the host itself was calm")):
        data, _ = _starved_run(steps, host_of)
        assert all(cause == "" for _, cause in _causes(data, "check")), (why, _causes(data, "check"))


def t_the_class_reads_the_window_up_to_the_failure_not_what_followed():
    hosts = []

    def calm_until_the_shot(clock):
        hosts.append(_Host(clock))
        return hosts[-1]

    class SlowFail:
        def __contains__(self, name):
            hosts[-1].stall(7.0)             # the host starves the unit only during the shot
            _Paged.clock.t += 8.0
            return name == "restart-lobby.png"
    steps = [{"id": "open", "role": "page:rc", "do": "open", "args": {"path": "/rc"}},
             {"id": "check", "role": "rec", "do": "go", "at": "after:open", "expect": ["relay.up"], "timeout": "4s",
              "shot": {"name": "restart-lobby", "role": "page:rc"}}]
    data, _ = _starved_run(steps, calm_until_the_shot, page={"fail_shots": SlowFail()})
    causes = dict(_causes(data, "check"))
    assert causes["expect 'relay.up' still false after 4s"] == "", \
        f"the check failed in a calm window; the stall after it is not its cause: {causes}"
    assert causes["shot restart-lobby"] == engine.STARVED, causes


def t_a_unit_at_its_own_memory_cap_is_not_a_starved_host():
    data, _ = _starved_run([{"id": "check", "role": "rec", "do": "go", "expect": ["relay.up"], "timeout": "4s"}],
                           lambda c: _Host(c, unit_io=0.5, capped=True))
    assert _causes(data, "check") == [("expect 'relay.up' still false after 4s", engine.AT_CAP)], \
        f"memory.max events in the window: the run's own memory, not the host: {_causes(data, 'check')}"


def t_a_slow_shot_is_named_under_the_role_that_took_it():
    class SlowShots:
        def __contains__(self, name):
            _Paged.clock.t += 3.0
            return False
    data, _, _ = _diag_run([{"id": "open", "role": "page:rc", "do": "open", "args": {"path": "/rc"}},
                            {"id": "box", "role": "rec", "do": "go", "at": "after:open",
                             "shot": {"name": "lobby", "role": "page:rc"}}], page={"fail_shots": SlowShots()})
    slow = [f for f in data["findings"] if f["message"].startswith("slow:")]
    assert [(f["step"], f["role"]) for f in slow] == [("box", "page:rc")], slow


def t_our_blocked_script_in_a_sandboxed_frame_is_no_page_error():
    class Msg:
        type, location = "error", {"url": "about:blank"}
        text = builtin.SANDBOX_BLOCKED + " and the 'allow-scripts' permission is not set."
    for trace, want in (("fail", ["soft"]), ("off", ["soft"])):
        br = _FakeBrowser()
        role = builtin.PageRole(engine.RunContext(urls={"relay": "http://127.0.0.1:9"}, browser=br, trace=trace),
                                "page:producer")
        role.open()
        role.do("open", {"path": "/"})
        br.page.handlers["console"](Msg())
        br.page.handlers["console"](Msg())
        closed = role.close()
        assert [sev for sev, _ in closed] == want and "2 " in closed[0][1], \
            f"trace {trace}: our script in a sandboxed frame is no page error, but it is counted: {closed}"


def t_a_trace_chunk_per_step_is_kept_only_for_a_failed_step():
    steps = [{"id": "open", "role": "page:rc", "do": "open", "args": {"path": "/rc"}},
             {"id": "ok", "role": "rec", "do": "go", "at": "after:open"},
             {"id": "bad", "role": "page:rc", "do": "open", "args": {"path": "x"}, "at": "after:ok"},
             {"id": "slow", "role": "paged", "do": "slow", "args": {"click": 3}, "at": "after:ok"}]
    data, br, tmp = _diag_run(steps, trace="fail")
    calls = br.tracing.calls
    assert calls[0] == ("start", True, True) and calls[-1] == ("stop",), calls
    stops = [c[1] for c in calls if c[0] == "stop_chunk"]
    assert stops == [None, None, "bad-page_rc.zip", "slow-page_rc.zip"], \
        f"a chunk per step, kept for a failed step and one with a slow action: {stops}"
    assert _by_id(data)["bad"]["traces"] == ["traces/bad-page_rc.zip"], _by_id(data)["bad"]
    assert os.path.isfile(os.path.join(tmp, "traces", "bad-page_rc.zip"))
    data, br, _ = _diag_run(steps[:3], trace="all")
    assert [c[1] for c in br.tracing.calls if c[0] == "stop_chunk"] == ["open-page_rc.zip", "ok-page_rc.zip",
                                                                       "bad-page_rc.zip"], br.tracing.calls
    data, br, _ = _diag_run(steps, trace="off")
    assert br.tracing.calls == [], "no trace unless asked"


class _State:
    def __init__(self):
        self.grabber = self
        self.snap = {"scene": "Stint", "preview": None, "stream": {"active": False}, "record": {"active": False},
                     "program": {"feeds": [{"input": "Feed A", "url": "http://127.0.0.1:41000", "occluded": False},
                                           {"input": "Feed B", "url": "http://127.0.0.1:41001", "occluded": True}]}}

    def snapshot(self):
        return self.snap

    def frames(self, urls):
        return {u: (b"rgb", 4, 2) for u in urls}


class _Sim:
    def __init__(self):
        self.state = _State()


class _Stub:
    def channels(self):
        return {"comms1": {"code": 1}, "comms2": {"code": 2}}


def t_obs_probe_names_the_commentator_on_program():
    seen = []

    def decode(rgb, w, h):
        seen.append((rgb, w, h))
        return (1, 1)
    probe = probes.ObsProbe(_Sim(), _Stub(), decode)
    names = {"obs": probe}
    assert expect.truth("obs.scene == 'Stint' and obs.program_channels == ['comms1']", names)[0]
    feeds = expect.raw(probe.program_feeds)
    assert feeds[1]["channel"] is None, "an occluded feed is not read"
    assert expect.truth("obs.program_feeds[0].stint == 1", names)[0]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

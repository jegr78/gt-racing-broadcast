#!/usr/bin/env python3
"""The event-simulation engine (#1232): scenario loading, the expect language, the step
runner on a fake clock, the report and the stack's pure helpers. Nothing is spawned.
Run: python3 tests/test_event_sim_engine.py"""
import atexit
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = os.path.join(ROOT, "tools", "event-sim")
for _p in (SIM, os.path.join(SIM, "fakes"), os.path.join(ROOT, "src", "scripts")):
    sys.path.insert(0, _p)
import engine  # noqa: E402
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
                     ("qualifying_part", "yes")):
        _refused(go, f"event.{key}", event={key: bad})
    scn = _scn(go, event={"stints": 3, "track": "6e378b", "track_length_m": 1200, "cars": 6,
                          "classes": ["GT3", "GT4"], "parts": 2, "qualifying_part": True, "seed": 7})
    assert scn.event["parts"] == 2


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
                 "x if box else y", "f'{box}'"):
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
        self.shots.append(path)
        self.masks = list(mask)


class _FakeBrowser:
    problem = ""

    def __init__(self):
        self.page = _FakePage()

    def get(self):
        return self

    def new_context(self, viewport=None):
        outer = self
        self.viewport = viewport

        class C:
            def new_page(self):
                return outer.page

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

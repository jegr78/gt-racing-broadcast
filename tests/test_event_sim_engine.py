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
from roles import builtin  # noqa: E402

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
    calls = ("twice",)

    def __init__(self, value=0, data=None):
        self.value, self.data = value, expect.View(data if data is not None else {})

    def twice(self, n):
        return n * 2


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


def _run(steps, box=None, **top):
    Recorder.log = []
    clock = Clock()
    ctx = engine.RunContext()
    ctx.box = box or Box()
    tmp = tempfile.mkdtemp(prefix="event-sim-test-")
    atexit.register(shutil.rmtree, tmp, True)
    eng = engine.Engine(_scn(steps, **top), ctx, {"box": ctx.box}, registry=_reg(), clock=clock,
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
    assert scenario.parse_at("lap:leader=3").condition == "race.lap >= 3"
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


def t_missing_values_are_unresolved_not_false():
    names = {"box": Box(1, {"a": 1})}
    for text in ("box.data.b == 1", "box.nothing", "box.data.a.b", "box.data[3]", "box.value < 'x'"):
        try:
            expect.truth(text, names)
        except expect.Unresolved:
            continue
        raise AssertionError(f"{text!r} must stay unresolved")


# -- engine ----------------------------------------------------------------------
def t_steps_run_in_order_no_earlier_than_their_trigger():
    data, clock, _ = _run([{"id": "a", "role": "rec", "do": "go", "at": "10s"},
                           {"id": "b", "role": "rec:x", "do": "go", "at": "after:a+5s"},
                           {"id": "c", "role": "rec", "do": "go", "at": "1s"}])
    started = {s["id"]: s["started"] for s in data["steps"]}
    assert started["a"] >= 10 and started["b"] >= started["a"] + 5, started
    assert started["c"] >= started["b"], "a past time starts at once, in file order"
    assert data["ok"] and [n for n, _ in Recorder.log] == ["rec", "rec:x", "rec"]


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


def t_race_triggers_wait_for_their_condition():
    race = Box(0)
    race.resolve = lambda name: {"lap": race.value, "state": "racing"}[name]
    clock = Clock()

    def sleep(s):
        clock.sleep(s)
        race.value = int(clock.t - 1000) // 10
    ctx = engine.RunContext()
    eng = engine.Engine(_scn([{"role": "rec", "do": "go", "at": "lap:leader=3"},
                              {"id": "never", "role": "rec", "do": "go", "at": "state:finished", "wait": "30s"}]),
                        ctx, {"race": race}, registry=_reg(), clock=clock, sleep=sleep)
    data = eng.run()
    assert data["steps"][0]["status"] == "ok" and data["steps"][0]["started"] >= 30, data["steps"][0]
    assert data["steps"][1]["status"] == "failed" and "never met" in data["steps"][1]["error"]


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
    assert "navigation in progress" in data["steps"][0]["checks"][0]["detail"]


def t_run_timeout_skips_the_rest():
    data, _, _ = _run([{"id": "a", "role": "rec", "do": "go", "at": "20s"},
                       {"id": "b", "role": "rec", "do": "go", "at": "8s", "expect": ["box.value == 1"]},
                       {"id": "c", "role": "rec", "do": "go"}], timeout="10s")
    status = {s["id"]: s["status"] for s in data["steps"]}
    assert status == {"a": "failed", "b": "failed", "c": "skipped"}, status
    assert data["steps"][0]["error"].endswith("past the run timeout")
    assert any(f["message"].startswith("run timeout") for f in data["findings"]), data["findings"]


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


def t_tracebacks_in_a_log_are_hard():
    text = "INFO up\nTraceback (most recent call last):\n  File \"x\", line 1\nValueError: bad\nINFO on\n"
    assert stack.log_findings(text, "relay") == [("hard", "relay log: traceback ValueError: bad")]
    assert stack.log_findings("ERROR [streamlink] gone\n", "relay") == []


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

    def screenshot(self, path, full_page=False):
        self.shots.append(path)


class _FakeBrowser:
    problem = ""

    def __init__(self):
        self.page = _FakePage()

    def get(self):
        return self

    def new_context(self, viewport=None):
        outer = self

        class C:
            def new_page(self):
                return outer.page

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


def t_page_role_without_a_browser_is_soft():
    ctx = engine.RunContext(urls={"relay": "http://127.0.0.1:9"})
    role = builtin.PageRole(ctx, "page")
    role.open()
    assert role.do("open", {"path": "/"}) is None
    assert ctx.findings and ctx.findings[0][0] == "soft"
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

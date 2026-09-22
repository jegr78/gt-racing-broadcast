#!/usr/bin/env python3
"""Stdlib unit checks for the first-time setup wizard logic
(src/scripts/init_setup.py). Run: python3 tests/test_init.py"""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "src", "scripts")
sys.path.insert(0, SCRIPTS)
spec = importlib.util.spec_from_file_location(
    "init_setup", os.path.join(SCRIPTS, "init_setup.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


# Arg parsing.

def t_parse_defaults():
    assert m.parse_init_args([]) == \
        {"browser": "firefox", "skip_installs": False, "force": False}


def t_parse_browser_both_forms():
    assert m.parse_init_args(["--browser", "chrome"])["browser"] == "chrome"
    assert m.parse_init_args(["--browser=chrome"])["browser"] == "chrome"


def t_parse_flags():
    opts = m.parse_init_args(["--skip-installs", "--force"])
    assert opts["skip_installs"] is True and opts["force"] is True


def t_parse_unknown_raises():
    _raises(lambda: m.parse_init_args(["--bogus"]))
    _raises(lambda: m.parse_init_args(["extra"]))
    _raises(lambda: m.parse_init_args(["--browser"]))   # missing value
    _raises(lambda: m.parse_init_args(["--browser="]))  # empty value


# The step plan.

def t_plan_full_order():
    assert m.build_plan() == ["profile", "env", "install-tools", "install-apps",
                              "cookies", "graphics", "media", "setup",
                              "export-companion", "preflight"]


def t_plan_skip_installs():
    plan = m.build_plan(skip_installs=True)
    assert "install-tools" not in plan and "install-apps" not in plan
    assert plan[0] == "profile" and plan[-1] == "preflight" and len(plan) == 8


def t_every_step_has_a_label():
    for key in m.build_plan():
        assert m.STEP_LABELS[key]


def t_step_kinds_cover_every_step():
    # every ordered step has a kind descriptor; kinds are the three the UI knows
    assert set(m.STEP_KINDS) == set(m.STEP_ORDER)
    for _key, meta in m.STEP_KINDS.items():
        assert meta["kind"] in ("gate", "job", "action")
        assert set(meta) <= {"kind", "op", "instruction"}


def t_step_kinds_jobs_name_a_real_op():
    # job steps carry the op name the UI POSTs to /api/op/<op>
    jobs = {k: meta for k, meta in m.STEP_KINDS.items() if meta["kind"] == "job"}
    assert jobs["cookies"]["op"] == "cookies"
    assert jobs["preflight"]["op"] == "preflight"
    # gate/action steps have no op
    assert m.STEP_KINDS["profile"]["kind"] == "gate"
    assert m.STEP_KINDS["profile"].get("op") is None
    assert m.STEP_KINDS["env"]["kind"] == "action"
    assert m.STEP_KINDS["env"].get("op") is None
    assert m.STEP_KINDS["export-companion"]["kind"] == "action"


def t_profile_step_points_at_demo_for_a_smoke_test():
    # the profile gate offers the shipped `demo` profile as a zero-config
    # smoke-test path with a public read-only Sheet pre-filled. (#206)
    instr = m.STEP_KINDS["profile"]["instruction"]
    assert "demo" in instr


# Done-detection.

def t_tools_done():
    tools = ("yt-dlp", "ffmpeg")
    assert m.tools_done(lambda t: "/bin/" + t, tools) is not None
    assert m.tools_done(lambda t: None, tools) is None
    assert m.tools_done(lambda t: "/bin/x" if t == "ffmpeg" else None, tools) is None


def t_apps_done():
    apps = ("obs", "discord")
    assert m.apps_done(lambda a: True, apps) is not None
    assert m.apps_done(lambda a: a == "obs", apps) is None


def t_cookies_done():
    assert m.cookies_done("PASS", "fresh (2 h old)") == "yt-cookies.txt fresh (2 h old)"
    assert m.cookies_done("WARN", "14 h old") is None


def t_assets_done():
    assert m.assets_done([], 12) == "complete (12 file(s))"
    assert m.assets_done(["Standby.png"], 11) is None
    assert m.assets_done(None, 5) is None   # sheet unreachable -> run the step


def t_setup_done():
    # done iff the import JSON exists and is newer than every dependency
    assert m.setup_done(100.0, [50.0, 60.0]) is not None
    assert m.setup_done(None, [50.0]) is None          # no import JSON yet
    assert m.setup_done(100.0, [150.0, 60.0]) is None  # collection newer
    assert m.setup_done(100.0, [100.0]) is None        # equal counts as stale
    assert m.setup_done(100.0, [None, 50.0]) is not None  # absent dep ignored


def t_export_done():
    assert m.export_done(True) is not None
    assert m.export_done(False) is None


# Gate, wizard and text.

def t_gate_pause_interactive_prompts():
    seen = []
    m.gate_pause("Fill in .env", True, ask=seen.append)
    assert seen and "Fill in .env" in seen[0] and "Enter" in seen[0]


def t_gate_pause_non_tty_exits():
    # checkpoint-and-exit: SystemExit whose payload carries the instruction
    try:
        m.gate_pause("Fill in .env", False)
    except SystemExit as e:
        assert "Fill in .env" in str(e.code) and "racecast init" in str(e.code)
        return
    raise AssertionError("expected SystemExit")


def t_fmt_step():
    assert m.fmt_step(4, 9, "cookies", "SKIP (fresh)") == \
        "[4/9] cookies … SKIP (fresh)"


def _step(key, done=None, code=0, log=None):
    return {"key": key, "label": key,
            "done": lambda: done,
            "run": lambda: (log.append(key), code)[1] if log is not None else code}


def t_wizard_skips_done_steps():
    log, out = [], []
    steps = [_step("a", done="cached", log=log), _step("b", log=log)]
    assert m.run_wizard(steps, False, out.append) == (0, True)
    assert log == ["b"]
    assert out[0] == "[1/2] a … SKIP (cached)" and "running" in out[1]


def t_wizard_force_runs_everything():
    log = []
    steps = [_step("a", done="cached", log=log), _step("b", log=log)]
    assert m.run_wizard(steps, True, lambda s: None) == (0, True)
    assert log == ["a", "b"]


def t_wizard_stops_on_failure():
    log, out = [], []
    steps = [_step("a", code=3, log=log), _step("b", log=log)]
    code, finished = m.run_wizard(steps, False, out.append)
    assert (code, finished) == (3, False)
    assert log == ["a"]                       # b never ran
    assert any("racecast init" in line for line in out)   # re-run hint printed


def t_wizard_last_step_failure_finishes():
    # preflight (last step) may exit 1; that is a verdict, not an abort
    steps = [_step("a"), _step("preflight", code=1)]
    assert m.run_wizard(steps, False, lambda s: None) == (1, True)


def t_wizard_all_skipped():
    steps = [_step("a", done="x"), _step("b", done="y")]
    assert m.run_wizard(steps, False, lambda s: None) == (0, True)


def t_manual_next_steps():
    lines = m.manual_next_steps("/rt/import.json", "/rt/buttons.companionconfig")
    text = " ".join(lines)
    assert "/rt/import.json" in text and "/rt/buttons.companionconfig" in text
    assert "Tailscale" in text and len(lines) == 3


def t_profile_done():
    assert m.profile_done("demo", "SHEET123") == "profile 'demo' ready"
    assert m.profile_done("demo", "") is None
    assert m.profile_done(None, "SHEET123") is None
    assert m.profile_done(None, "") is None


def t_prompt_value_returns_stripped_answer_when_tty():
    assert m.prompt_value("Name", True, ask=lambda _p: "  erf  ") == "erf"


def t_prompt_value_checkpoints_when_not_tty():
    try:
        m.prompt_value("Name", False)
        raise AssertionError("expected SystemExit")
    except SystemExit:
        pass


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

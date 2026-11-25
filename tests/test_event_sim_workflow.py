#!/usr/bin/env python3
"""Checks .github/workflows/event-sim.yml against the bundled scenarios. Run: python3 tests/test_event_sim_workflow.py"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "event-sim.yml")
SCENARIOS = os.path.join(ROOT, "tools", "event-sim", "scenarios")


def _text():
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


def _choices(text, name):
    """The `options:` list of the workflow_dispatch input `name`."""
    block = re.search(r"^      %s:\n((?:        .*\n)+)" % re.escape(name), text, re.M)
    assert block, "input %r not found" % name
    opts = re.search(r"options:\n((?:\s+- .*\n)+)", block.group(1))
    assert opts, "input %r has no options" % name
    return [line.strip()[2:].strip() for line in opts.group(1).splitlines()]


def t_the_scenario_input_offers_every_bundled_scenario():
    bundled = sorted(f[:-5] for f in os.listdir(SCENARIOS) if f.endswith(".toml"))
    offered = sorted(_choices(_text(), "scenario"))
    assert offered == bundled, "scenario choices %s != bundled scenarios %s" % (offered, bundled)


def t_the_scenario_input_defaults_to_quick():
    m = re.search(r"^      scenario:\n(?:        .*\n)*?        default: (\S+)", _text(), re.M)
    assert m and m.group(1).strip("\"'") == "quick", "scenario default is %r" % (m and m.group(1))


def t_the_profile_input_is_quick_or_full():
    assert _choices(_text(), "profile") == ["quick", "full"], _choices(_text(), "profile")


def t_it_runs_only_when_dispatched():
    on = re.search(r"^on:\n((?:  .*\n|\n)+?)^\S", _text(), re.M)
    assert on, "no on: block"
    triggers = re.findall(r"^  (\w[\w-]*):", on.group(1), re.M)
    assert triggers == ["workflow_dispatch"], "triggers %s" % triggers


def t_the_timeout_follows_the_profile_or_the_full_scenario():
    want = "timeout-minutes: ${{ (inputs.profile == 'full' || inputs.scenario == 'full') && 120 || 30 }}"
    assert want in _text(), "120 min for the full profile or the full scenario, else 30"


def t_the_report_is_kept_fourteen_days_even_after_a_failure():
    text = _text()
    upload = re.search(r"uses: actions/upload-artifact@[0-9a-f]{40}.*\n((?:\s{8,}.*\n)+)", text)
    assert upload, "no pinned upload-artifact step"
    assert "retention-days: 14" in upload.group(1), upload.group(1)
    assert re.search(r"if: (\$\{\{ )?always\(\)", text), "the upload must run after a failed scenario"


def t_inputs_reach_the_shell_only_through_env():
    for run in re.findall(r"run: \|\n((?:\s{10,}.*\n)+)|run: (.*)\n", _text()):
        body = run[0] or run[1]
        assert "${{" not in body, "an expression inside run: is a script injection path: %r" % body


def t_it_cannot_write_to_the_repository():
    assert re.search(r"^permissions:\n  contents: read\n", _text(), re.M), "token must be read-only"


def _job(text, name):
    """The body of job `name` under jobs:, up to the next job."""
    m = re.search(r"^  %s:\n((?:    .*\n|\n)+)" % re.escape(name), text, re.M)
    assert m, "job %r not found" % name
    return m.group(1)


def t_every_dispatch_records_smoke_with_the_obs_stand_in_and_renders_a_few_seconds():
    job = _job(_text(), "event-video")
    assert "if:" not in job.split("steps:")[0], "the film pipeline check runs on every dispatch"
    rec = re.search(r"run: python tools/event-sim/event_sim\.py (.*)\n", job)
    assert rec, "the job records a scenario"
    argv = rec.group(1).split()
    assert argv[argv.index("--scenario") + 1] == "smoke", "it records the short smoke scenario: %s" % argv
    assert "--record" in argv and argv[argv.index("--obs") + 1] == "sim", "record mode with the OBS stand-in: %s" % argv
    render = re.search(r"run: python tools/build-event-video\.py (.*)\n", job)
    assert render, "the job renders the recording"
    rargv = render.group(1).split()
    assert rargv[0] == argv[argv.index("--record") + 1], "it renders the dir it recorded: %s" % rargv
    assert "--to" in rargv and float(rargv[rargv.index("--to") + 1]) <= 10, "a partial film of a few seconds: %s" % rargv
    assert re.search(r"timeout-minutes: \d+", job.split("steps:")[0]), "the job has its own time limit"
    upload = re.search(r"uses: actions/upload-artifact@[0-9a-f]{40}.*\n((?:\s{8,}.*\n)+)", job)
    assert upload and "if: ${{ always() }}" in job, "the film and the report are uploaded even after a failure"
    assert "*.mp4" in upload.group(1) and "retention-days: 14" in upload.group(1), upload and upload.group(1)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("t_") and callable(f)]
    for fn in tests:
        fn()
    print("ALL PASS (%d)" % len(tests))

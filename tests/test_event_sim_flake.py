#!/usr/bin/env python3
"""The event simulation's flake tools: host and cgroup state (`hoststate.py`) and the
repeat summary and the bounded CPU load (`flake.py`). Fake /proc and cgroup trees, no
real unit, nothing spawned. Run: python3 tests/test_event_sim_flake.py"""
import atexit
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = os.path.join(ROOT, "tools", "event-sim")
for _p in (SIM, os.path.join(SIM, "fakes"), os.path.join(ROOT, "src", "scripts"), os.path.join(ROOT, "tools")):
    sys.path.insert(0, _p)
import flake  # noqa: E402
import hoststate  # noqa: E402


def _tmp():
    path = tempfile.mkdtemp(prefix="event-sim-flake-")
    atexit.register(shutil.rmtree, path, True)
    return path


def _write(base, rel, text):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _psi(some_total, full_total=0):
    return (f"some avg10=1.50 avg60=0.75 avg300=0.10 total={some_total}\n"
            f"full avg10=0.00 avg60=0.00 avg300=0.00 total={full_total}\n")


def _host(usage, throttled, unit_cpu, host_cpu, oom=0, high=0):
    """A fake /proc and cgroup tree with the unit at `/app.slice/racecast-e2e.service`."""
    base = _tmp()
    proc, cg = os.path.join(base, "proc"), os.path.join(base, "cg")
    _write(proc, "loadavg", "2.50 1.25 0.75 3/400 12345\n")
    _write(proc, "self/cgroup", "0::/app.slice/racecast-e2e.service\n")
    for res in ("cpu", "memory", "io"):
        _write(proc, f"pressure/{res}", _psi(host_cpu if res == "cpu" else 0))
    unit = "app.slice/racecast-e2e.service"
    _write(cg, f"{unit}/cpu.stat", f"usage_usec {usage}\nuser_usec 1\nsystem_usec 2\nnr_periods 10\n"
                                   f"nr_throttled {throttled // 1000}\nthrottled_usec {throttled}\n")
    _write(cg, f"{unit}/memory.current", "1048576\n")
    _write(cg, f"{unit}/memory.peak", "2097152\n")
    _write(cg, f"{unit}/memory.max", "2147483648\n")
    _write(cg, f"{unit}/memory.events", f"low 0\nhigh {high}\nmax 0\noom {oom}\noom_kill {oom}\n")
    _write(cg, f"{unit}/pids.current", "42\n")
    for res in ("cpu", "memory", "io"):
        _write(cg, f"{unit}/{res}.pressure", _psi(unit_cpu if res == "cpu" else 0))
    return proc, cg


def t_a_sample_reads_load_pressure_and_the_units_cgroup():
    proc, cg = _host(usage=3_000_000, throttled=500_000, unit_cpu=250_000, host_cpu=900_000, high=2)
    s = hoststate.sample(proc=proc, cgroup_root=cg, clock=lambda: 10.0)
    assert s["loadavg"] == [2.5, 1.25, 0.75], s["loadavg"]
    assert s["pressure"]["cpu"]["some"] == {"avg10": 1.5, "avg60": 0.75, "avg300": 0.1, "total": 900_000}, s["pressure"]
    unit = s["unit"]
    assert unit["path"] == "/app.slice/racecast-e2e.service", unit
    assert unit["cpu"]["usage_usec"] == 3_000_000 and unit["cpu"]["throttled_usec"] == 500_000, unit["cpu"]
    assert (unit["memory_current"], unit["memory_peak"], unit["memory_max"]) == (1048576, 2097152, 2147483648), unit
    assert unit["memory_events"]["high"] == 2 and unit["pids_current"] == 42, unit
    assert unit["pressure"]["cpu"]["some"]["total"] == 250_000, unit["pressure"]


def t_a_sample_without_linux_files_is_none_everywhere():
    empty = _tmp()
    s = hoststate.sample(proc=os.path.join(empty, "proc"), cgroup_root=os.path.join(empty, "cg"))
    assert s["loadavg"] is None and s["unit"] is None, s
    assert s["pressure"] == {"cpu": None, "memory": None, "io": None}, s["pressure"]
    assert hoststate.delta(s, s) == dict.fromkeys(hoststate.DELTA_KEYS), "nothing to compare is None, not 0"


def t_a_delta_says_how_much_cpu_the_unit_got_and_how_long_it_stalled():
    proc, cg = _host(usage=1_000_000, throttled=0, unit_cpu=100_000, host_cpu=0)
    before = hoststate.sample(proc=proc, cgroup_root=cg, clock=lambda: 10.0)
    proc2, cg2 = _host(usage=3_500_000, throttled=750_000, unit_cpu=2_100_000, host_cpu=4_000_000, oom=1, high=3)
    after = hoststate.sample(proc=proc2, cgroup_root=cg2, clock=lambda: 14.0)
    d = hoststate.delta(before, after)
    assert d["seconds"] == 4.0 and d["cpu_s"] == 2.5 and d["throttled_s"] == 0.75, d
    assert d["nr_throttled"] == 750, d
    assert d["unit_cpu_stall_s"] == 2.0 and d["host_cpu_stall_s"] == 4.0, d
    assert d["cpu_stall_share"] == 0.5, "the unit's tasks waited for a CPU half of the time"
    assert d["oom"] == 1 and d["high"] == 3, d


def _stat(pid, comm, utime, stime):
    fields = ["S", "1"] + ["0"] * 9 + [str(utime), str(stime)] + ["0"] * 30
    return f"{pid} ({comm}) {' '.join(fields)}\n"


def t_the_units_processes_are_named_and_ranked_by_cpu():
    proc, cg = _host(usage=0, throttled=0, unit_cpu=0, host_cpu=0)
    _write(cg, "app.slice/racecast-e2e.service/cgroup.procs", "11\n12\n13\n14\n")
    _write(proc, "11/stat", _stat(11, "chrome-headless", 300, 100))
    _write(proc, "11/cmdline", "/x/chrome-headless-shell\0--type=gpu-process\0--no-sandbox\0")
    _write(proc, "12/stat", _stat(12, "python", 50, 50))
    _write(proc, "12/cmdline", "/venv/bin/python\0/app/src/racecast.py\0relay\0")
    _write(proc, "13/stat", _stat(13, "ffm peg) x", 900, 0))
    _write(proc, "13/cmdline", "ffmpeg\0-i\0pipe:0\0")
    got = hoststate.processes(proc=proc, cgroup_root=cg, ticks=100)
    assert [(p["name"], p["cpu_s"]) for p in got] == [("ffmpeg", 9.0), ("chrome-headless-shell gpu-process", 4.0),
                                                       ("python racecast.py", 1.0)], \
        f"by CPU seconds, a vanished pid left out: {got}"
    empty = _tmp()
    assert hoststate.processes(proc=os.path.join(empty, "p"), cgroup_root=os.path.join(empty, "c")) is None


def t_the_load_is_cpu_with_a_bounded_worker_count():
    assert flake.parse_load("cpu:2") == ("cpu", 2)
    for bad in ("cpu", "cpu:0", f"cpu:{flake.MAX_LOAD + 1}", "io:1", "cpu:x", "cpu:-1"):
        try:
            flake.parse_load(bad)
            raise AssertionError(bad)
        except ValueError:
            pass  # expected


def t_the_load_runs_its_workers_supervised_and_stops_them():
    made = []

    class Child:
        def __init__(self, name, argv, env, cwd, log_path, seconds):
            self.name, self.argv, self.seconds, self.stopped = name, argv, seconds, False
            made.append(self)

        def stop(self):
            self.stopped = True
            return ""
    work = _tmp()
    load = flake.Load(3, child=Child)
    load.start(work, seconds=600)
    assert [c.name for c in made] == ["load-cpu-1", "load-cpu-2", "load-cpu-3"], made
    assert all(c.seconds == 600 and c.argv[-1] == flake.BUSY for c in made), "each worker spins until stopped"
    assert load.stop() == [] and all(c.stopped for c in made), "stop ends every worker"


def _report(ok, steps, findings=()):
    return {"scenario": "red-flag", "ok": ok, "duration": 300.0,
            "steps": [{"id": i, "role": "r", "do": "d", "status": st, "duration": d, "error": "",
                       "actions": acts} for i, st, d, acts in steps],
            "findings": [{"severity": "hard", "step": s, "role": "r", "message": m} for s, m in findings]}


def _shot(s):
    return {"role": "race_control", "kind": "shot", "what": "restart-lobby", "s": s, "ok": True, "depth": 0}


def t_the_summary_counts_runs_steps_and_the_timing_tail():
    runs = [
        {"run": 1, "code": 0, "report": _report(True, [("to_box", "ok", 3.0, [_shot(0.2)]), ("next3", "ok", 5.0, [])])},
        {"run": 2, "code": 1, "report": _report(False, [("to_box", "ok", 11.0, [_shot(8.0)]),
                                                        ("next3", "failed", 9.0, [])],
                                                [("to_box", "shot restart-lobby: Timeout 8000ms")])},
        {"run": 3, "code": 0, "report": _report(True, [("to_box", "ok", 4.0, [_shot(0.4)]), ("next3", "ok", 6.0, [])])},
        {"run": 4, "code": 3, "report": None},
    ]
    s = flake.summarize(runs)
    assert [r["status"] for r in s["runs"]] == ["pass", "fail", "pass", "not-run"], s["runs"]
    assert s["counts"] == {"pass": 2, "fail": 1, "not-run": 1}, s["counts"]
    assert s["runs"][1]["failed_steps"] == ["next3", "to_box"], "a step with a hard finding failed too"
    assert s["runs"][1]["hard"] == ["to_box: shot restart-lobby: Timeout 8000ms"], s["runs"][1]
    to_box = s["steps"]["to_box"]
    assert (to_box["n"], to_box["min"], to_box["median"], to_box["max"], to_box["failed"]) == (3, 3.0, 4.0, 11.0, 1), to_box
    assert to_box["p95"] == 11.0, "nearest rank: the slowest of three"
    shot = s["actions"]["race_control shot restart-lobby"]
    assert (shot["n"], shot["min"], shot["median"], shot["max"]) == (3, 0.2, 0.4, 8.0), shot


def t_the_summary_is_written_as_json_and_html():
    out = _tmp()
    s = flake.summarize([{"run": 1, "code": 0, "report": _report(True, [("a", "ok", 1.0, [])])}])
    path = flake.write(out, s, {"scenario": "<x>", "load": "cpu:2"})
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
        assert json.load(fh)["meta"]["load"] == "cpu:2"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert "&lt;x&gt;" in text and "run-01" in text, "escaped, and each run links its report"


def t_a_busy_unit_is_waited_for_before_a_run():
    answers = [0, 0, 3]      # systemctl is-active: active, active, inactive
    slept = []

    def run(argv, **kw):
        assert "is-active" in argv and flake.UNIT in argv, argv
        return type("R", (), {"returncode": answers.pop(0)})()
    t = [0.0]

    def sleep(s):
        slept.append(s)
        t[0] += s
    assert flake.wait_for_unit(run=run, sleep=sleep, clock=lambda: t[0], limit=600, platform="linux")
    assert len(slept) == 2, "it waited twice, then the unit was free"
    answers[:] = [0] * 100
    assert not flake.wait_for_unit(run=run, sleep=sleep, clock=lambda: t[0], limit=60, platform="linux"), \
        "a unit busy past the limit is reported, not waited for forever"
    assert flake.wait_for_unit(run=None, platform="darwin"), "no unit outside Linux"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

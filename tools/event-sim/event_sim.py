#!/usr/bin/env python3
"""Run an event-simulation scenario and write its report.

    python3 tools/event-sim/event_sim.py --scenario smoke --out DIR

Stands up the simulated Sheet, webhook sink, stream stub and OBS, then the relay and
the Control Center in simulation mode on the demo profile, runs the scenario's steps
and writes DIR/report.html, DIR/report.json, DIR/shots/ and DIR/logs/. Exit code 0:
every hard check passed; 1: a hard failure; 2: the scenario or the stack could not
start. `--check` only loads and validates the scenario. `--repeat N` runs it N times, one
contained run after the other, and writes DIR/summary.html and DIR/summary.json over
DIR/run-NN/ (tools/CLAUDE.md, hunting a flake). Maintainer tool, not shipped."""
import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (HERE, os.path.join(HERE, "fakes"), os.path.join(ROOT, "src", "scripts"), os.path.join(ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import e2e_safety  # noqa: E402
import flake  # noqa: E402
import logsetup  # noqa: E402
import roles  # noqa: E402
import scenario  # noqa: E402
import stack  # noqa: E402

SCENARIOS = os.path.join(HERE, "scenarios")
MAX_REPEAT = 50
WORK_NOTE = "work-dir.txt"   # names the run's own work dir while it exists
TRACES = ("off", "fail", "all")


def _repeat_count(text):
    n = int(text)
    if not 1 <= n <= MAX_REPEAT:
        raise argparse.ArgumentTypeError(f"--repeat is 1 to {MAX_REPEAT}")
    return n


def _load(text):
    try:
        flake.parse_load(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def build_parser():
    ap = argparse.ArgumentParser(description="Run an event-simulation scenario and write its report.")
    ap.add_argument("--scenario", required=True, help="a bundled scenario name or a path to a .toml file")
    ap.add_argument("--out", help="report directory (required unless --check)")
    ap.add_argument("--check", action="store_true", help="only load and validate the scenario")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the temporary app home and fake state after the run")
    ap.add_argument("--headed", action="store_true", help="show the browser (local only)")
    ap.add_argument("--no-skips", action="store_true",
                    help="a skipped step is a hard failure (a run that must have its browser)")
    ap.add_argument("--trace", choices=TRACES, default="off",
                    help="Playwright traces: none (default), the failed and slow steps', or every step's")
    ap.add_argument("--repeat", type=_repeat_count, metavar="N",
                    help="run the scenario N times, one contained run after the other, and summarize")
    ap.add_argument("--load", type=_load, metavar="cpu:K",
                    help=f"K busy workers (1-{flake.MAX_LOAD}) inside the run, sharing its CPU quota")
    return ap


@contextlib.contextmanager
def owned_work(out, keep=False):
    """The run's own work dir: named in OUT/work-dir.txt while it exists, so a run that
    was killed is cleaned up by that path and never by a pattern (another run may be
    using its own). TMPDIR points into it, so the browser's temporary files go with it.
    Removed at the end unless `keep`. An OUT that already names a work dir is refused:
    that one belongs to an earlier run, which the caller removes by its path."""
    os.makedirs(out, exist_ok=True)
    note = os.path.join(out, WORK_NOTE)
    try:
        with open(note, encoding="utf-8") as fh:
            raise RuntimeError(f"{note} names {fh.read().strip()}, the work dir of an earlier run in this "
                               "out dir: remove that path and the note, or use a fresh --out")
    except FileNotFoundError:
        pass  # no earlier run left its work dir here
    before = os.environ.get("TMPDIR")
    work = tempfile.mkdtemp(prefix="racecast-sim-")
    try:
        with open(note, "w", encoding="utf-8") as fh:
            fh.write(work + "\n")
        tmp = os.path.join(work, "tmp")
        os.makedirs(tmp)
        os.environ["TMPDIR"] = tmp
        yield work
    finally:
        if before is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = before
        if keep:
            print(f"event-sim: work dir kept at {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
            with contextlib.suppress(FileNotFoundError):
                os.remove(note)


def run(scn, out, keep_work=False, headed=False, no_skips=False, trace="off", load=None):
    with owned_work(out, keep=keep_work) as work:
        return _run(scn, out, work, no_skips=no_skips, headed=headed, trace=trace, load=load)


def _run(scn, out, work, headed=False, no_skips=False, trace="off", load=None):
    import browser
    import engine
    import report
    st = stack.Stack(scn, work)
    br = browser.Browser(headless=not headed)
    busy = flake.Load(flake.parse_load(load)[1]) if load else None
    data = None
    try:
        try:
            st.start()
        except Exception as exc:
            print(f"event-sim: the stack did not start: {exc}", file=sys.stderr)
            return 2
        if busy:
            busy.start(work, stack.lifetime(scn))
        ctx = engine.RunContext(urls=st.urls, token=st.token, stub=st.stub, obs=st.obs,
                                browser=br, out_dir=out, event=scn.event, event_link=st.event_link,
                                secrets=[st.secret], kill_relay=st.kill_relay, trace=trace)
        eng = engine.Engine(scn, ctx, st.probes(), registry=roles.default_registry(),
                            shots_dir=os.path.join(out, "shots"), final_checks=[st.log_checks],
                            no_skips=no_skips, traces_dir=os.path.join(out, "traces"))
        try:
            data = eng.run()
        except Exception:
            data = eng.data([r for r in eng.results.values()])
            data["ok"] = False
            data["findings"].insert(0, {"severity": "hard", "role": "", "step": "",
                                        "message": ctx.scrub("engine error: " + traceback.format_exc(limit=3))})
    finally:
        br.close()
        problems = []
        try:
            problems += busy.stop() if busy else []
        except Exception as exc:  # noqa: BLE001  the stack still stops and the report is still written
            problems.append(f"load workers: {type(exc).__name__}: {exc}")
        problems += st.stop(logs_to=os.path.join(out, "logs"))
        if data is not None:
            data["setup"] = {"load": load, "trace": trace}
            for p in problems:
                data["findings"].append({"severity": "soft", "role": "", "step": "", "message": ctx.scrub(p)})
            report.write(out, data)
    hard = [f for f in data["findings"] if f["severity"] == "hard"]
    soft = [f for f in data["findings"] if f["severity"] == "soft"]
    for s in data["steps"]:
        print(f"  {s['status']:<8} {s['id']:<24} {s['role']}.{s['do']}" + (f"  {s['error']}" if s["error"] else ""))
    for f in hard:
        print(f"  HARD  {f.get('step') or f.get('role') or 'run'}: {f['message']}")
    skipped = sum(1 for s in data["steps"] if s["status"] == "skipped")
    print(f"event-sim: {scn.name} {'PASS' if data['ok'] else 'FAIL'}: {len(data['steps'])} steps "
          f"({skipped} skipped), {len(hard)} hard, {len(soft)} soft findings; "
          f"report {os.path.join(out, 'report.html')}")
    return 0 if data["ok"] else 1


def _run_argv(scn, out, args):
    """The argv of one contained run."""
    return (["--scenario", scn.path, "--out", out] + ["--keep-work"] * args.keep_work
            + ["--headed"] * args.headed + ["--no-skips"] * args.no_skips + ["--trace", args.trace]
            + (["--load", args.load] if args.load else []))


def _sha():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              errors="replace", timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def repeat(scn, out, args, enter=e2e_safety.enter, wait=flake.wait_for_unit):
    """Run the scenario `args.repeat` times into out/run-NN, each in its own contained
    run after the one before, then write the summary. A run that wrote no report
    counts as not run. 0 when every run passed; 2 when OUT already holds a run-NN, whose
    report would pass for this set's."""
    taken = [d for d in (f"run-{i:02d}" for i in range(1, args.repeat + 1)) if os.path.exists(os.path.join(out, d))]
    if taken:
        print(f"event-sim: {out} already holds {', '.join(taken)}; use a fresh --out", file=sys.stderr)
        return 2
    runs = []
    for i in range(1, args.repeat + 1):
        run_out = os.path.join(out, f"run-{i:02d}")
        if not wait():
            print(f"event-sim: run {i}: the E2E unit stayed busy", file=sys.stderr)
            runs.append({"run": i, "code": None, "report": None})
            continue
        argv = _run_argv(scn, run_out, args)
        try:
            code = enter(__file__, argv, seconds=stack.lifetime(scn), long=True)
        except RuntimeError as exc:      # the admission refused it, e.g. host memory pressure
            print(f"event-sim: run {i}: not started: {exc}", file=sys.stderr)
            runs.append({"run": i, "code": None, "report": None})
            continue
        if code is None:
            code = run(scn, run_out, keep_work=args.keep_work, headed=args.headed, no_skips=args.no_skips,
                       trace=args.trace, load=args.load)
        try:
            with open(os.path.join(run_out, "report.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None
        runs.append({"run": i, "code": code, "report": data})
        print(f"event-sim: run {i}/{args.repeat}: "
              f"{'no report' if data is None else ('PASS' if data.get('ok') and code == 0 else 'FAIL')} (exit {code})")
    summary = flake.summarize(runs)
    meta = {"scenario": scn.name, "runs": args.repeat, "load": args.load, "trace": args.trace,
            "no_skips": args.no_skips, "sha": _sha()}
    path = flake.write(out, summary, meta)
    print(f"event-sim: {scn.name} x{args.repeat}: {summary['counts']}; summary {path}")
    for sid, d in summary["steps"].items():
        if d["failed"]:
            print(f"  {sid}: failed {d['failed']}x")
    return 0 if summary["counts"]["pass"] == args.repeat else 1


def main(argv=None):
    logsetup.harden_stdio()
    args = build_parser().parse_args(argv)
    try:
        scn = scenario.load(scenario.find(args.scenario, SCENARIOS), roles.default_registry())
    except (scenario.ScenarioError, OSError, ValueError) as exc:
        print(f"event-sim: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"event-sim: {scn.name} is valid ({len(scn.steps)} steps)")
        return 0
    if not args.out:
        print("event-sim: --out DIR is required", file=sys.stderr)
        return 2
    out = os.path.abspath(args.out)
    if args.repeat:
        return repeat(scn, out, args)
    # The contained run starts in tools/, so it gets absolute paths.
    try:
        contained = e2e_safety.enter(__file__, _run_argv(scn, out, args), seconds=stack.lifetime(scn), long=True)
    except RuntimeError as exc:          # no containment, or the host under memory pressure
        print(f"event-sim: not started: {exc}", file=sys.stderr)
        return 2
    if contained is not None:
        return contained
    try:
        return run(scn, out, keep_work=args.keep_work, headed=args.headed, no_skips=args.no_skips,
                   trace=args.trace, load=args.load)
    except RuntimeError as exc:          # the out dir names an earlier run's work dir
        print(f"event-sim: not started: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

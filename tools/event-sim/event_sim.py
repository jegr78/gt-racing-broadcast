#!/usr/bin/env python3
"""Run an event-simulation scenario and write its report.

    python3 tools/event-sim/event_sim.py --scenario smoke --out DIR

Stands up the simulated Sheet, webhook sink, stream stub and OBS, then the relay and
the Control Center in simulation mode on the demo profile, runs the scenario's steps
and writes DIR/report.html, DIR/report.json, DIR/shots/ and DIR/logs/. Exit code 0:
every hard check passed; 1: a hard failure; 2: the scenario or the stack could not
start. `--check` only loads and validates the scenario. Maintainer tool, not shipped."""
import argparse
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (HERE, os.path.join(HERE, "fakes"), os.path.join(ROOT, "src", "scripts"), os.path.join(ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logsetup  # noqa: E402
import roles  # noqa: E402
import scenario  # noqa: E402

SCENARIOS = os.path.join(HERE, "scenarios")


def build_parser():
    ap = argparse.ArgumentParser(description="Run an event-simulation scenario and write its report.")
    ap.add_argument("--scenario", required=True, help="a bundled scenario name or a path to a .toml file")
    ap.add_argument("--out", help="report directory (required unless --check)")
    ap.add_argument("--check", action="store_true", help="only load and validate the scenario")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the temporary app home and fake state after the run")
    ap.add_argument("--headed", action="store_true", help="show the browser (local only)")
    return ap


def run(scn, out, keep_work=False, headed=False):
    import browser
    import engine
    import report
    import stack
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="racecast-sim-")
    st = stack.Stack(scn, work)
    br = browser.Browser(headless=not headed)
    data = None
    try:
        try:
            st.start()
        except Exception as exc:
            print(f"event-sim: the stack did not start: {exc}", file=sys.stderr)
            return 2
        ctx = engine.RunContext(urls=st.urls, token=st.token, stub=st.stub, obs=st.obs,
                                browser=br, out_dir=out)
        eng = engine.Engine(scn, ctx, st.probes(), registry=roles.default_registry(),
                            shots_dir=os.path.join(out, "shots"), final_checks=[st.log_checks])
        try:
            data = eng.run()
        except Exception:
            data = eng.data([r for r in eng.results.values()])
            data["ok"] = False
            data["findings"].insert(0, {"severity": "hard", "role": "", "step": "",
                                        "message": "engine error: " + traceback.format_exc(limit=3)})
    finally:
        br.close()
        problems = st.stop(logs_to=os.path.join(out, "logs"))
        if data is not None:
            for p in problems:
                data["findings"].append({"severity": "soft", "role": "", "step": "", "message": p})
            report.write(out, data)
        if keep_work:
            print(f"event-sim: work dir kept at {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    hard = [f for f in data["findings"] if f["severity"] == "hard"]
    soft = [f for f in data["findings"] if f["severity"] == "soft"]
    for s in data["steps"]:
        print(f"  {s['status']:<8} {s['id']:<24} {s['role']}.{s['do']}" + (f"  {s['error']}" if s["error"] else ""))
    for f in hard:
        print(f"  HARD  {f.get('step') or f.get('role') or 'run'}: {f['message']}")
    print(f"event-sim: {scn.name} {'PASS' if data['ok'] else 'FAIL'}: {len(data['steps'])} steps, "
          f"{len(hard)} hard, {len(soft)} soft findings; report {os.path.join(out, 'report.html')}")
    return 0 if data["ok"] else 1


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
    import e2e_safety
    import stack
    # The contained run starts in tools/, so it gets absolute paths.
    contained = e2e_safety.enter(__file__, ["--scenario", scn.path, "--out", out]
                                 + ["--keep-work"] * args.keep_work + ["--headed"] * args.headed,
                                 seconds=stack.lifetime(scn), long=True)
    if contained is not None:
        return contained
    return run(scn, out, keep_work=args.keep_work, headed=args.headed)


if __name__ == "__main__":
    sys.exit(main())

"""Flake hunting: a bounded CPU load inside the run, and the summary of repeated runs.

`event_sim.py --repeat N` runs a scenario N times, one E2E unit run after the other, and
`summarize` turns their reports into pass/fail per run and the duration distribution
of every step and every top-level page action (min, median, p95, max). `--load cpu:K`
starts K busy workers inside the run, so they share the unit's CPU quota with the
relay and the browser."""
import html
import json
import math
import os
import statistics
import subprocess
import sys
import time

import e2e_safety
import stack

MAX_LOAD = 4             # busy workers; the unit has two CPUs
BUSY = "while True: pass"
UNIT = e2e_safety.UNIT
UNIT_POLL_S = 15.0


def parse_load(text):
    """"cpu:K" -> ("cpu", K) with 1 <= K <= MAX_LOAD."""
    kind, _, count = str(text).partition(":")
    if kind != "cpu" or not count.isdigit() or not 1 <= int(count) <= MAX_LOAD:
        raise ValueError(f"--load is cpu:K with K from 1 to {MAX_LOAD}, not {text!r}")
    return kind, int(count)


class Load:
    """K busy workers, each a supervised child that ends with the run."""

    def __init__(self, workers, child=stack.Child):
        self.workers, self._child, self.children = workers, child, []

    def start(self, work, seconds, env=None):
        for i in range(1, self.workers + 1):
            name = f"load-cpu-{i}"
            self.children.append(self._child(name, [sys.executable, "-c", BUSY], dict(env or os.environ), work,
                                             os.path.join(work, name + ".log"), seconds))

    def stop(self):
        """Stop every worker; returns the problems."""
        problems = [c.stop() for c in self.children]
        self.children = []
        return [p for p in problems if p]


def wait_for_unit(run=subprocess.run, sleep=time.sleep, clock=time.monotonic, limit=1800.0,
                  platform=sys.platform, poll=UNIT_POLL_S):
    """Wait while another run holds the machine's E2E unit; False when it is still busy
    after `limit` seconds. Outside Linux there is no unit."""
    if platform != "linux":
        return True
    begin = clock()
    while True:
        try:
            busy = run(["systemctl", "--user", "is-active", "--quiet", UNIT],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except OSError:
            return True       # no systemd: the run itself reports a missing unit
        if not busy:
            return True
        if clock() - begin >= limit:
            return False
        sleep(poll)


def _dist(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "min": None, "median": None, "p95": None, "max": None}
    p95 = values[max(0, math.ceil(0.95 * len(values)) - 1)]
    return {"n": len(values), "min": values[0], "median": round(statistics.median(values), 3),
            "p95": p95, "max": values[-1]}


def summarize(runs):
    """`runs`: [{run, code, report}] (report None when the run wrote none). Returns
    {runs, counts, steps, actions}."""
    out, counts, durations, failed, actions = [], {"pass": 0, "fail": 0, "not-run": 0}, {}, {}, {}
    for r in runs:
        data = r.get("report")
        if data is None:
            status, bad, hard, duration = "not-run", [], [], None
        else:
            status = "pass" if data.get("ok") else "fail"
            hard_findings = [f for f in data.get("findings", []) if f.get("severity") == "hard"]
            bad = sorted({s["id"] for s in data["steps"] if s["status"] == "failed"}
                         | {f["step"] for f in hard_findings if f.get("step")})
            hard = [f"{f.get('step') or f.get('role') or 'run'}: {f['message']}" for f in hard_findings][:5]
            duration = data.get("duration")
            for s in data["steps"]:
                if s["status"] in ("ok", "failed"):
                    durations.setdefault(s["id"], []).append(s["duration"])
                for a in s.get("actions") or []:
                    if a.get("depth", 0) == 0 and a.get("s") is not None:
                        actions.setdefault(f"{a['role']} {a['kind']} {a['what']}", []).append(a["s"])
            for step in bad:
                failed[step] = failed.get(step, 0) + 1
        counts[status] += 1
        out.append({"run": r["run"], "code": r.get("code"), "status": status, "duration": duration,
                    "failed_steps": bad, "hard": hard})
    steps = {sid: dict(_dist(v), failed=failed.get(sid, 0)) for sid, v in durations.items()}
    for sid, n in failed.items():
        steps.setdefault(sid, dict(_dist([]), failed=n))
    return {"runs": out, "counts": counts, "steps": steps,
            "actions": {k: _dist(v) for k, v in sorted(actions.items())}}


def _e(value):
    return html.escape("" if value is None else str(value))


def _row(cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def write(out_dir, summary, meta):
    """Write summary.json and summary.html into `out_dir`; returns the HTML path."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, **summary}, fh, indent=2, default=str)
        fh.write("\n")

    def dist(d):
        return [_e(d.get(k)) for k in ("n", "min", "median", "p95", "max")]
    runs = "".join(_row([f"<a href='run-{r['run']:02d}/report.html'>run-{r['run']:02d}</a>", _e(r["status"]),
                         _e(r["duration"]), _e(", ".join(r["failed_steps"])),
                         "<br>".join(_e(h) for h in r["hard"])]) for r in summary["runs"])
    steps = "".join(_row([_e(sid), *dist(d), _e(d["failed"])]) for sid, d in summary["steps"].items())
    acts = "".join(_row([_e(k), *dist(d)]) for k, d in
                   sorted(summary["actions"].items(), key=lambda kv: -(kv[1]["max"] or 0)))
    head = "<th>n</th><th>min</th><th>median</th><th>p95</th><th>max</th>"
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Event simulation repeat</title>"
            f"<style>body{{font:14px/1.4 system-ui,sans-serif;margin:16px}}table{{border-collapse:collapse}}"
            f"td,th{{border:1px solid #ccc;padding:3px 6px;text-align:left}}</style></head><body>"
            f"<h1>{_e(meta.get('scenario'))}: {_e(summary['counts'])}</h1>"
            f"<p>{_e(json.dumps(meta))}</p>"
            f"<h2>Runs</h2><table><tr><th>Run</th><th>Result</th><th>Duration</th><th>Failed steps</th>"
            f"<th>Hard findings</th></tr>{runs}</table>"
            f"<h2>Step durations (s)</h2><table><tr><th>Step</th>{head}<th>failed</th></tr>{steps}</table>"
            f"<h2>Page actions (s), slowest first</h2><table><tr><th>Action</th>{head}</tr>{acts}</table>"
            f"</body></html>\n")
    path = os.path.join(out_dir, "summary.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path

"""The step runner: triggers, actions, checks, screenshots and findings.

`Engine(scenario, ctx, probes).run()` runs each step once its `at` trigger fires, one
action at a time, and returns the report data (see report.py). It never spawns anything:
`stack.Stack` builds the context and the probes, tests pass fakes and a fake clock."""
import os
import re
import time
from dataclasses import asdict, dataclass, field

import expect
import hoststate
import redaction
import roles
import roles.builtin
from scenario import TEMPLATE_RE as _TEMPLATE, template_refs

HARD, SOFT = "hard", "soft"
EVENT_LINKS = ("invite", "timing")
LEARN_EVERY_S = 1.0
scrub = redaction.scrub
POLL_S = 0.25


@dataclass
class Finding:
    severity: str
    message: str
    step: str = ""
    role: str = ""


@dataclass
class Check:
    expr: str
    ok: bool
    soft: bool
    elapsed: float
    detail: str = ""


@dataclass
class StepResult:
    id: str
    role: str
    do: str
    status: str = "pending"          # ok, failed, skipped
    started: float = None            # seconds since the run started
    duration: float = 0.0
    error: str = ""
    checks: list = field(default_factory=list)
    shot: str = ""
    fail_shot: str = ""              # the role's page when the step failed
    result: object = None
    actions: list = field(default_factory=list)    # each page action and shot with its seconds
    host: dict = field(default_factory=dict)       # hoststate.delta over the step
    snapshots: list = field(default_factory=list)  # the state at a failure
    traces: list = field(default_factory=list)     # Playwright trace zips kept of this step
    long_tasks: dict = field(default_factory=dict) # role -> its page's long tasks, for a slow or failed step
    cpu: list = None                               # each process's CPU seconds over a slow or failed step


class RunContext:
    """What roles see of the run. `urls` maps service names (relay, ui, obs, sheet,
    sink) to base URLs; `token(name)` mints a console token for a crew member."""

    def __init__(self, urls=None, token=None, stub=None, obs=None, browser=None, out_dir="",
                 event=None, event_link=None, secrets=(), clock=time.monotonic, kill_relay=None,
                 trace="off"):
        self.urls = dict(urls or {})
        self.trace = trace               # Playwright tracing: "off", "fail" (keep failed steps) or "all"
        self._token = token
        self.stub = stub
        self.obs = obs
        self._browser = browser
        self.out_dir = out_dir
        self.event = dict(event or {})
        self._event_link = event_link
        self._kill_relay = kill_relay
        self.secrets = set(secrets)      # every credential of the run, for `scrub`
        self._clock, self._learned_at = clock, None
        self.findings = []

    def learn(self, force=False):
        """Note the event's current invite and timing ids as credentials, whoever read
        them: an `api` answer carries them bare. A renewed link is learned too and the
        old one kept. Read at most once per LEARN_EVERY_S unless `force`."""
        now = self._clock()
        if self._event_link is None or (not force and self._learned_at is not None
                                        and now - self._learned_at < LEARN_EVERY_S):
            return
        self._learned_at = now
        for kind in EVENT_LINKS:
            try:
                link = self._event_link(kind)
            except Exception:
                continue  # no readable link is no credential to hide
            if isinstance(link, str) and link:
                self.secrets.add(link)

    def scrub(self, value):
        """`value` without credentials: the one filter for everything the run writes."""
        self.learn()
        return redaction.scrub(value, self.secrets)

    def url(self, service):
        if service not in self.urls:
            raise roles.StepError(f"unknown service {service!r} (known: {', '.join(sorted(self.urls))})")
        return self.urls[service]

    def token(self, name):
        if self._token is None:
            raise roles.StepError("this run mints no console tokens")
        token = self._token(name)
        self.secrets.add(token)
        return token

    def event_link(self, kind):
        """The id of the event's driver invite ("invite") or public timing link
        ("timing"). It is a credential: never put it into a result or a finding."""
        link = self._event_link(kind) if self._event_link is not None else None
        if not link:
            raise roles.StepError(f"the event has no {kind} link yet (Start event creates it)")
        self.secrets.add(link)
        return link

    def kill_relay(self):
        """Kill the run's relay as a crash would; returns its PID."""
        if self._kill_relay is None:
            raise roles.StepError("this run cannot kill its relay")
        try:
            return self._kill_relay()
        except (RuntimeError, OSError) as exc:
            raise roles.StepError(str(exc)) from None

    def browser(self):
        """The shared headless browser (`browser.Browser`), or None without Playwright."""
        return self._browser.get() if self._browser is not None else None

    def browser_problem(self):
        if self._browser is None:
            return "this run has no browser"
        return self._browser.problem or "the browser did not start"

    def finding(self, severity, message):
        self.findings.append((severity, message))


def resolve_args(value, results):
    """`value` with every `${step.key.sub}` replaced from earlier step results. A string
    that is exactly one placeholder keeps the value's type."""
    if isinstance(value, dict):
        return {k: resolve_args(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_args(v, results) for v in value]
    if not isinstance(value, str):
        return value

    def lookup(path):
        head, *rest = path.split(".")
        if head not in results:
            raise roles.StepError(f"${{{path}}}: no earlier step {head!r} with a result")
        cur = results[head]
        for part in rest:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                raise roles.StepError(f"${{{path}}}: {part!r} is not in the result of {head!r}")
        return cur

    whole = _TEMPLATE.fullmatch(value)
    if whole:
        return lookup(whole.group(1))
    return _TEMPLATE.sub(lambda m: str(lookup(m.group(1))), value)


class Engine:
    def __init__(self, scenario, ctx, probes, registry=None, clock=time.monotonic,
                 sleep=time.sleep, poll=POLL_S, shots_dir=None, final_checks=(), no_skips=False,
                 traces_dir=None, host=hoststate.sample, processes=hoststate.processes):
        self.scenario = scenario
        self.ctx = ctx
        self.probes = dict(probes)
        self.registry = registry if registry is not None else roles.default_registry()
        self.clock, self.sleep, self.poll = clock, sleep, poll
        self.shots_dir = shots_dir
        self.final_checks = list(final_checks)
        self.no_skips = no_skips
        self.traces_dir = traces_dir
        self._host = host
        self._processes = processes
        self.unit_processes = None       # the run's processes and their CPU at the end
        self._step_sample = {}
        self.roles = {}
        self.pages = {}                  # each page role's memory when it closed
        self.findings = []
        self.results = {}
        self.start = None

    # -- helpers ------------------------------------------------------------
    def now(self):
        return self.clock() - self.start

    def _names(self):
        names = dict(self.probes)
        names["page"] = expect.Func(self._page)
        names["result"] = expect.Func(self._result)
        return names

    def _result(self, step_id):
        res = self.results.get(step_id)
        if res is None or res.result is None:
            raise expect.Unresolved(f"step {step_id!r} has no result yet")
        return res.result

    def _page(self, role):
        if role not in self.roles:
            raise expect.Unresolved(f"role {role!r} has not acted yet")
        return self.roles[role].page()

    def _role(self, name):
        if name not in self.roles:
            cls = self.registry[name.split(":", 1)[0]]
            role = cls(self.ctx, name)
            self.roles[name] = role
            role.open()
        return self.roles[name]

    def _take_findings(self, step):
        for severity, message in self.ctx.findings:
            self.findings.append(Finding(severity, message, step.id, step.role))
        self.ctx.findings.clear()

    def wait_for(self, text, timeout):
        """(ok, elapsed, detail): poll `text` until it is true or `timeout` passes."""
        begin = self.clock()
        tree = expect.compile_expr(text)
        detail = ""
        while True:
            try:
                value = expect.raw(expect.evaluate(tree, self._names()))
                if value:
                    return True, self.clock() - begin, ""
                detail = expect.explain(tree, self._names())
            except expect.Unresolved as exc:
                detail = str(exc)
            except Exception as exc:  # a role's page() mid-navigation: not true yet
                detail = f"{type(exc).__name__}: {exc}"
            elapsed = self.clock() - begin
            if elapsed >= timeout:
                return False, elapsed, detail
            self.sleep(min(self.poll, timeout - elapsed))

    # -- triggers -----------------------------------------------------------
    def _check_once(self, text):
        try:
            return bool(expect.raw(expect.evaluate(expect.compile_expr(text), self._names())))
        except Exception:  # unresolved or a page mid-navigation: not fired yet
            return False

    def _trigger_state(self, step):
        """("run", ""), ("wait", ""), ("skip", why) or ("fail", why) for a pending step.
        A trigger is armed at its earliest moment (its time, the referenced step's end
        plus the offset, or the run start); `wait` counts from then."""
        now, end, at = self.now(), self.scenario.run_seconds, step.at
        refs = template_refs(step.args) | ({at.ref} if at.kind == "after" else set())
        failed = sorted(r for r in refs if r in self.results and self.results[r].status != "ok")
        if failed:
            return "skip", f"skipped: {', '.join(failed)} did not succeed"
        if at.kind == "after" and at.ref not in self.results:
            armed, origin = None, ""
        elif at.kind == "after":
            ref = self.results[at.ref]
            armed, origin = ref.started + ref.duration + at.seconds, f"the end of {at.ref}"
        elif at.kind == "time":
            armed, origin = at.seconds, f"{at.seconds:g}s"
        else:
            armed, origin = 0.0, "the run start"
        if armed is not None and armed > end:
            return "fail", f"trigger at {armed:.1f}s is past the end of the run ({end:g}s)"
        if now >= end:
            return "fail", f"the run ended ({self.scenario.max_minutes} min) before the step ran"
        unfinished = [r for r in refs if r not in self.results]
        if armed is None or now < armed:
            return "wait", ""
        if not unfinished and (at.kind != "race" or self._check_once(at.condition)):
            return "run", ""
        if step.wait is not None and now >= armed + step.wait:
            what = at.condition if at.kind == "race" else f"after {', '.join(sorted(unfinished))}"
            return "fail", f"trigger {what!r} did not fire within {step.wait:g}s of {origin}"
        return "wait", ""

    def _settle(self, step, status, why):
        res = StepResult(step.id, step.role, step.do, status=status, error=why,
                         started=round(self.now(), 3))
        self.results[step.id] = res
        if status == "failed":
            self.findings.append(Finding(HARD, why, step.id, step.role))
        return res

    def _shoot(self, step, res):
        shot = step.shot
        if not shot:
            return
        role_name = shot.role or step.role
        if self.shots_dir is None:
            self.findings.append(Finding(HARD if self.no_skips else SOFT,
                                         f"shot {shot.name}: no shots directory", step.id, role_name))
            return
        os.makedirs(self.shots_dir, exist_ok=True)
        path = os.path.join(self.shots_dir, shot.name + ".png")
        host, procs = self._sample(), self._unit_processes()
        begin = self.clock()
        rec = {"role": role_name, "kind": "shot", "what": shot.name, "s": None, "ok": False, "depth": 0,
               "budget": roles.builtin.budget_for("shot"), "covered": False}
        res.actions.append(rec)
        try:
            self._role(role_name).shot(path, shot.element)
            res.shot = f"shots/{shot.name}.png"
            rec["ok"] = True
        except Exception as exc:  # ShotUnavailable or a broken page: a screenshot never decides the run
            rec["s"] = round(self.clock() - begin, 3)
            self._shot_load(rec, host, procs)
            why = f"shot {shot.name}: {exc}"
            # A run that must have its browser (--no-skips) must have its pictures too.
            self.findings.append(Finding(HARD if self.no_skips else SOFT, why, step.id, role_name))
            if not isinstance(exc, roles.ShotUnavailable):
                self._snapshot(step, res, role_name, why, f"fail-{step.id}-shot.png")
        if rec["s"] is None:
            rec["s"] = round(self.clock() - begin, 3)
            self._shot_load(rec, host, procs)

    def _shot_load(self, rec, host, procs):
        """What the unit got during the shot itself: a step's figures dilute it."""
        rec["host"] = hoststate.delta(host, self._sample())
        rec["cpu"] = hoststate.cpu_delta(procs, self._unit_processes(), top=8)

    def _sample(self):
        try:
            return self._host() or {}
        except Exception:  # noqa: BLE001  a diagnostic never decides the run
            return {}

    def _unit_processes(self):
        try:
            return self._processes()
        except Exception:  # noqa: BLE001  a diagnostic never decides the run
            return None

    def _snapshot(self, step, res, role_name, why, picture):
        """The state at a failure: the host and the unit, and for a page role its page
        (`PageRole.diagnose`) and a picture of the whole page as `picture`. Never decides the run."""
        host = self._sample()
        snap = {"role": role_name, "why": why, "at": round(self.now(), 3), "host": host,
                "since_step": hoststate.delta(self._step_sample, host), "processes": self._unit_processes(),
                "page": None,
                "picture": "", "picture_s": None, "picture_error": ""}
        res.snapshots.append(snap)
        role = self.roles.get(role_name)
        if not isinstance(role, roles.builtin.PageRole) or role.pg is None:
            return
        try:
            snap["page"] = role.diagnose()
        except Exception as exc:  # noqa: BLE001  a page that broke with the step
            snap["page"] = {"error": f"{type(exc).__name__}: {exc}"}
        if self.shots_dir is None:
            return
        os.makedirs(self.shots_dir, exist_ok=True)
        begin = self.clock()
        try:
            role.shot(os.path.join(self.shots_dir, picture))
            snap["picture"] = f"shots/{picture}"
            res.fail_shot = res.fail_shot or snap["picture"]
        except Exception as exc:  # a page that broke with the step
            snap["picture_error"] = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            self.findings.append(Finding(SOFT, f"no picture of the failure: {exc}", step.id, role_name))
        snap["picture_s"] = round(self.clock() - begin, 3)

    def _page_roles(self):
        return [(n, r) for n, r in self.roles.items() if isinstance(r, roles.builtin.PageRole)]

    def _trace_begin(self, step):
        for name, role in self._page_roles():
            try:
                role.trace_begin()
            except Exception as exc:  # noqa: BLE001  a trace never decides the run
                self.findings.append(Finding(SOFT, f"trace of {name}: {exc}", step.id, name))

    def _trace_end(self, step, res):
        """End every page's chunk; kept as traces/<step>-<role>.zip for a step that
        failed, left a snapshot or had a slow action (a flake's tail), or for every step
        with trace "all"."""
        keep = self.traces_dir is not None and (self.ctx.trace == "all" or self._noteworthy(res))
        for name, role in self._page_roles():
            file = f"{step.id}-{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.zip"
            try:
                if role.trace_end(os.path.join(self.traces_dir, file) if keep else None):
                    res.traces.append(f"traces/{file}")
            except Exception as exc:  # noqa: BLE001  a trace never decides the run
                self.findings.append(Finding(SOFT, f"trace of {name}: {exc}", step.id, name))

    @staticmethod
    def _noteworthy(res):
        """A step that failed, left a snapshot or had a slow action: its diagnostics are kept."""
        return res.status == "failed" or bool(res.snapshots) or any(a.get("slow") for a in res.actions)

    def _long_tasks(self, res, seconds):
        """Each open page's long tasks during a step that failed, left a snapshot or was slow."""
        if not self._noteworthy(res):
            return
        for name, role in self._page_roles():
            if role.pg is None:
                continue
            try:
                res.long_tasks[name] = role.long_tasks(seconds)
            except Exception as exc:  # noqa: BLE001  a page that does not answer
                res.long_tasks[name] = [{"error": str(exc).splitlines()[0] if str(exc) else type(exc).__name__}]

    def _take_actions(self, res):
        for name, role in self._page_roles():
            res.actions.extend(dict(a, role=name) for a in role.take_actions())

    def _flag_slow(self, step, res):
        """A soft finding for each action over its budget whose slowness no inner action explains."""
        for a in res.actions:
            if a["budget"] is not None and a["s"] is not None and a["s"] > a["budget"] and not a["covered"]:
                a["slow"] = True
                self.findings.append(Finding(SOFT, f"slow: {a['role']} {a['kind']} {a['what']!r} took "
                                                   f"{a['s']:.1f}s (budget {a['budget']:g}s)", step.id, step.role))

    def _checks(self, step, res):
        slow = self.scenario.tolerances.get("state_s", 2.0)
        for soft, exprs in ((False, step.expect), (True, step.soft)):
            for text in exprs:
                left = max(0.0, self.scenario.run_seconds - self.now())
                ok, elapsed, detail = self.wait_for(text, min(step.timeout, left))
                if not ok and left < step.timeout:
                    detail += " (cut at the end of the run)"
                res.checks.append(Check(text, ok, soft, round(elapsed, 3), detail))
                if not ok:
                    sev = SOFT if soft else HARD
                    self.findings.append(Finding(sev, f"expect {text!r} still false after "
                                                 f"{step.timeout:g}s: {detail}", step.id, step.role))
                elif elapsed > slow:
                    self.findings.append(Finding(SOFT, f"expect {text!r} took {elapsed:.1f}s "
                                                 f"(more than {slow:g}s)", step.id, step.role))

    def run_step(self, step):
        res = StepResult(step.id, step.role, step.do, started=round(self.now(), 3))
        self.results[step.id] = res
        begin = self.clock()
        self._step_sample = self._sample()
        step_procs = self._unit_processes()
        self._trace_begin(step)
        try:
            args = resolve_args(step.args, {k: r.result for k, r in self.results.items()
                                            if r.result is not None})
            res.result = self._role(step.role).do(step.do, args)
            res.status = "ok"
        except roles.Unavailable as exc:
            res.status, res.error = "skipped", str(exc)
            self.findings.append(Finding(SOFT, f"skipped: {exc}", step.id, step.role))
        except roles.StepError as exc:
            res.status, res.error = "failed", str(exc)
        except Exception as exc:  # a role bug must not hide the rest of the report
            res.status, res.error = "failed", f"{type(exc).__name__}: {exc}"
        if res.status == "failed":
            self.findings.append(Finding(HARD, res.error, step.id, step.role))
        elif res.status == "ok":
            self._checks(step, res)
            if any(not c.ok and not c.soft for c in res.checks):
                res.status = "failed"
        self._take_actions(res)
        if res.status != "skipped":
            self._shoot(step, res)
        if res.status == "failed":
            self._snapshot(step, res, step.role, res.error or "a check failed", f"fail-{step.id}.png")
        self._take_findings(step)
        self._flag_slow(step, res)
        self._long_tasks(res, self.clock() - begin)
        if self._noteworthy(res):
            res.cpu = hoststate.cpu_delta(step_procs, self._unit_processes())
        res.host = hoststate.delta(self._step_sample, self._sample())
        self._trace_end(step, res)
        self.ctx.learn(force=True)      # a link this step created or renewed
        res.duration = round(self.clock() - begin, 3)
        return res

    # -- the run ------------------------------------------------------------
    def run(self):
        """One executor: each tick runs the first pending step (file order) whose trigger
        fired, settles the ones that can no longer run, and otherwise waits a poll."""
        self.start = self.clock()
        pending, out = list(self.scenario.steps), []
        try:
            while pending:
                for step in pending:
                    state, why = self._trigger_state(step)
                    if state == "wait":
                        continue
                    pending.remove(step)
                    if state == "run":
                        out.append(self.run_step(step))
                    else:
                        out.append(self._settle(step, "skipped" if state == "skip" else "failed", why))
                    break
                else:
                    self.sleep(self.poll)
        finally:
            self.unit_processes = self._unit_processes()
            for name, role in reversed(list(self.roles.items())):
                memory = role.memory()          # catches the browser's errors itself
                if memory is not None:
                    self.pages[name] = memory
                try:
                    for severity, message in role.close() or []:
                        self.findings.append(Finding(severity, message, role=name))
                except Exception as exc:  # close every role
                    self.findings.append(Finding(SOFT, f"close failed: {exc}", role=name))
        for check in self.final_checks:
            for severity, message in check():
                self.findings.append(Finding(severity, message))
        return self.data(out)

    def data(self, steps):
        findings = list(self.findings)
        if self.no_skips:
            findings += [Finding(HARD, f"skipped, and this run allows no skips: {s.error}", s.id, s.role)
                         for s in steps if s.status == "skipped"]
        hard = [f for f in findings if f.severity == HARD]
        failed = [s for s in steps if s.status == "failed"]
        return self.ctx.scrub({
            "scenario": self.scenario.name, "description": self.scenario.description,
            "ok": not hard and not failed, "duration": round(self.now(), 3),
            "steps": [asdict(s) for s in steps], "pages": dict(self.pages),
            "unit_processes": self.unit_processes,
            "findings": [asdict(f) for f in sorted(findings, key=lambda f: f.severity != HARD)]})

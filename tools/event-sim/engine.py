"""The step runner: triggers, actions, checks, screenshots and findings.

`Engine(scenario, ctx, probes).run()` runs the steps in file order, each no earlier
than its `at`, and returns the report data (see report.py). It never spawns anything:
`stack.Stack` builds the context and the probes, tests pass fakes and a fake clock."""
import os
import re
import time
from dataclasses import asdict, dataclass, field

import expect
import roles
from roles import StepError

HARD, SOFT = "hard", "soft"
POLL_S = 0.25
_TEMPLATE = re.compile(r"\$\{([A-Za-z0-9._-]+)\}")


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
    result: object = None


class RunContext:
    """What roles see of the run. `urls` maps service names (relay, ui, obs, sheet,
    sink) to base URLs; `token(name)` mints a console token for a crew member."""

    def __init__(self, urls=None, token=None, stub=None, obs=None, browser=None, out_dir=""):
        self.urls = dict(urls or {})
        self._token = token
        self.stub = stub
        self.obs = obs
        self._browser = browser
        self.out_dir = out_dir
        self.findings = []

    def url(self, service):
        if service not in self.urls:
            raise StepError(f"unknown service {service!r} (known: {', '.join(sorted(self.urls))})")
        return self.urls[service]

    def token(self, name):
        if self._token is None:
            raise StepError("this run mints no console tokens")
        return self._token(name)

    def browser(self):
        """The shared headless browser (`browser.Browser`), or None without Playwright."""
        return self._browser.get() if self._browser is not None else None

    def browser_problem(self):
        if self._browser is None:
            return "this run has no browser"
        return self._browser.problem or "the browser did not start"

    def finding(self, severity, message):
        self.findings.append((severity, message))


def template_refs(value):
    """The step ids that `${...}` placeholders in `value` refer to."""
    if isinstance(value, dict):
        return set().union(*(template_refs(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(template_refs(v) for v in value)) if value else set()
    if isinstance(value, str):
        return {m.group(1).split(".", 1)[0] for m in _TEMPLATE.finditer(value)}
    return set()


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
            raise StepError(f"${{{path}}}: no earlier step {head!r} with a result")
        cur = results[head]
        for part in rest:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                raise StepError(f"${{{path}}}: {part!r} is not in the result of {head!r}")
        return cur

    whole = _TEMPLATE.fullmatch(value)
    if whole:
        return lookup(whole.group(1))
    return _TEMPLATE.sub(lambda m: str(lookup(m.group(1))), value)


class Engine:
    def __init__(self, scenario, ctx, probes, registry=None, clock=time.monotonic,
                 sleep=time.sleep, poll=POLL_S, shots_dir=None, final_checks=()):
        self.scenario = scenario
        self.ctx = ctx
        self.probes = dict(probes)
        self.registry = registry if registry is not None else roles.default_registry()
        self.clock, self.sleep, self.poll = clock, sleep, poll
        self.shots_dir = shots_dir
        self.final_checks = list(final_checks)
        self.roles = {}
        self.findings = []
        self.results = {}
        self.start = None

    # -- helpers ------------------------------------------------------------
    def now(self):
        return self.clock() - self.start

    def _names(self):
        names = dict(self.probes)
        names["page"] = expect.Func(self._page)
        return names

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

    def _deadline_left(self):
        return self.scenario.timeout - self.now()

    def _wait_until(self, t):
        while self.now() < t:
            self.sleep(min(self.poll, t - self.now()))

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

    # -- one step -----------------------------------------------------------
    def _trigger(self, step, res):
        """Wait for the step's trigger; returns an error text, or ""."""
        at = step.at
        if at.kind == "time":
            target = at.seconds
        elif at.kind == "after":
            ref = self.results[at.ref]
            if ref.status != "ok":
                res.status = "skipped"
                return f"skipped: {at.ref} did not succeed"
            target = ref.started + ref.duration + at.seconds
        elif at.kind == "race":
            ok, _elapsed, detail = self.wait_for(at.condition, min(step.wait, max(0.0, self._deadline_left())))
            return "" if ok else f"trigger {at.condition!r} never met: {detail}"
        else:
            return ""
        if target > self.scenario.timeout:
            return f"trigger at {target:.1f}s is past the run timeout"
        self._wait_until(target)
        return ""

    def _shoot(self, step, res):
        shot = step.shot
        if not shot:
            return
        role_name = shot.role or step.role
        if self.shots_dir is None:
            self.findings.append(Finding(SOFT, f"shot {shot.name}: no shots directory", step.id, role_name))
            return
        os.makedirs(self.shots_dir, exist_ok=True)
        path = os.path.join(self.shots_dir, shot.name + ".png")
        try:
            self._role(role_name).shot(path, shot.element)
            res.shot = f"shots/{shot.name}.png"
        except Exception as exc:  # ShotUnavailable or a broken page: a screenshot never decides the run
            self.findings.append(Finding(SOFT, f"shot {shot.name}: {exc}", step.id, role_name))

    def _checks(self, step, res):
        slow = self.scenario.tolerances.get("state_s", 2.0)
        for soft, exprs in ((False, step.expect), (True, step.soft)):
            for text in exprs:
                ok, elapsed, detail = self.wait_for(text, step.timeout)
                res.checks.append(Check(text, ok, soft, round(elapsed, 3), detail))
                if not ok:
                    sev = SOFT if soft else HARD
                    self.findings.append(Finding(sev, f"expect {text!r} still false after "
                                                 f"{step.timeout:g}s: {detail}", step.id, step.role))
                elif elapsed > slow:
                    self.findings.append(Finding(SOFT, f"expect {text!r} took {elapsed:.1f}s "
                                                 f"(more than {slow:g}s)", step.id, step.role))

    def run_step(self, step):
        res = StepResult(step.id, step.role, step.do)
        self.results[step.id] = res
        error = self._trigger(step, res)
        failed = sorted(r for r in template_refs(step.args)
                        if r in self.results and self.results[r].status != "ok")
        if failed and res.status != "skipped":
            res.status, error = "skipped", f"skipped: {', '.join(failed)} did not succeed"
        res.started = round(self.now(), 3)
        if res.status == "skipped":
            res.error = error
            return res
        begin = self.clock()
        try:
            if error:
                raise StepError(error)
            args = resolve_args(step.args, {k: r.result for k, r in self.results.items()
                                            if r.result is not None})
            res.result = self._role(step.role).do(step.do, args)
            res.status = "ok"
        except StepError as exc:
            res.status, res.error = "failed", str(exc)
        except Exception as exc:  # a role bug must not hide the rest of the report
            res.status, res.error = "failed", f"{type(exc).__name__}: {exc}"
        if res.status == "failed":
            self.findings.append(Finding(HARD, res.error, step.id, step.role))
        else:
            self._checks(step, res)
            if any(not c.ok and not c.soft for c in res.checks):
                res.status = "failed"
        self._shoot(step, res)
        self._take_findings(step)
        res.duration = round(self.clock() - begin, 3)
        return res

    # -- the run ------------------------------------------------------------
    def run(self):
        self.start = self.clock()
        out = []
        try:
            for step in self.scenario.steps:
                if self._deadline_left() <= 0:
                    res = StepResult(step.id, step.role, step.do, status="skipped",
                                     error="run timeout reached")
                    self.results[step.id] = res
                    out.append(res)
                    continue
                out.append(self.run_step(step))
            if any(r.error == "run timeout reached" for r in out):
                self.findings.append(Finding(HARD, f"run timeout of {self.scenario.timeout:g}s reached"))
        finally:
            for name, role in reversed(list(self.roles.items())):
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
        hard = [f for f in self.findings if f.severity == HARD]
        failed = [s for s in steps if s.status == "failed"]
        return {"scenario": self.scenario.name, "description": self.scenario.description,
                "ok": not hard and not failed, "duration": round(self.now(), 3),
                "steps": [asdict(s) for s in steps],
                "findings": [asdict(f) for f in sorted(self.findings, key=lambda f: f.severity != HARD)]}

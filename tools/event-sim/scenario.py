"""Load and validate an event-simulation scenario (TOML, read with tomllib).

A scenario has an optional `name`, `description` and `timeout`, an `[event]` table, an
optional `[sheet.tabs]` table of extra or replaced tabs, optional `[tolerances]` and a
list of `[[step]]` tables. Steps run in file order; `at` gives a step's earliest start.
See tools/CLAUDE.md (Event simulation engine) for the format."""
import os
import re
import tomllib
from dataclasses import dataclass, field

import expect

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
_AFTER = re.compile(r"^after:([A-Za-z0-9._-]+)(?:\+(.+))?$")
_LAP = re.compile(r"^lap:leader=(\d{1,4})$")
_STATE = re.compile(r"^state:([a-z_]{1,32})$")
DEFAULT_CHECK_TIMEOUT = 5.0
DEFAULT_RUN_TIMEOUT = 720.0        # leaves start, teardown and report inside a service's 900 s
DEFAULT_TRIGGER_WAIT = 600.0
STEP_KEYS = {"id", "at", "role", "do", "args", "expect", "soft", "timeout", "wait", "shot"}
TOP_KEYS = {"name", "description", "timeout", "event", "sheet", "tolerances", "step"}


class ScenarioError(ValueError):
    """A scenario that cannot run; the message names the step."""


def parse_duration(text):
    """Seconds from "500ms", "20s", "1m30s" or "2h"."""
    if not isinstance(text, str):
        raise ScenarioError(f"duration must be a string like '20s', not {text!r}")
    s = text.strip()
    pos, total = 0, 0.0
    for m in _DURATION_PART.finditer(s):
        if m.start() != pos:
            break
        total += float(m.group(1)) * _UNITS[m.group(2)]
        pos = m.end()
    if not s or pos != len(s):
        raise ScenarioError(f"not a duration: {text!r} (use e.g. '500ms', '20s', '1m30s')")
    return total


@dataclass(frozen=True)
class Trigger:
    """When a step may start: `kind` is "now", "time", "after" or "race"."""
    kind: str
    seconds: float = 0.0
    ref: str = ""
    condition: str = ""


def parse_at(text):
    if text is None or text == "":
        return Trigger("now")
    if not isinstance(text, str):
        raise ScenarioError(f"at must be a string, not {text!r}")
    m = _AFTER.match(text)
    if m:
        return Trigger("after", parse_duration(m.group(2)) if m.group(2) else 0.0, ref=m.group(1))
    m = _LAP.match(text)
    if m:
        return Trigger("race", condition=f"race.lap >= {int(m.group(1))}")
    m = _STATE.match(text)
    if m:
        return Trigger("race", condition=f"race.state == '{m.group(1)}'")
    return Trigger("time", parse_duration(text))


@dataclass(frozen=True)
class Shot:
    name: str
    role: str = ""
    element: str = ""


@dataclass
class Step:
    index: int
    id: str
    role: str
    do: str
    at: Trigger
    args: dict = field(default_factory=dict)
    expect: list = field(default_factory=list)
    soft: list = field(default_factory=list)
    timeout: float = DEFAULT_CHECK_TIMEOUT
    wait: float = DEFAULT_TRIGGER_WAIT
    shot: Shot = None

    @property
    def label(self):
        return f"step {self.index + 1} ({self.id})"


@dataclass
class Commentator:
    name: str
    channel: str
    stint: int
    scheduled: bool


@dataclass
class Scenario:
    name: str
    description: str
    timeout: float
    event: dict
    commentators: list
    tabs: dict
    tolerances: dict
    steps: list
    path: str = ""


def role_kind(role):
    return role.split(":", 1)[0]


def _commentators(event):
    out, seen = [], set()
    for i, raw in enumerate(event.get("commentators") or []):
        where = f"event.commentators[{i}]"
        if not isinstance(raw, dict):
            raise ScenarioError(f"{where} must be a table")
        name = raw.get("name")
        channel = raw.get("channel")
        stint = raw.get("stint", i + 1)
        if not isinstance(name, str) or not name.strip():
            raise ScenarioError(f"{where}.name must be a non-empty string")
        if not isinstance(channel, str) or not CHANNEL_RE.match(channel):
            raise ScenarioError(f"{where}.channel must match {CHANNEL_RE.pattern}")
        if not isinstance(stint, int) or not 1 <= stint <= 255:
            raise ScenarioError(f"{where}.stint must be an integer 1-255")
        if channel.lower() in seen:
            raise ScenarioError(f"{where}.channel {channel!r} is used twice")
        seen.add(channel.lower())
        out.append(Commentator(name.strip(), channel, stint, bool(raw.get("scheduled", False))))
    return out


def _tabs(sheet):
    tabs = (sheet or {}).get("tabs") or {}
    if not isinstance(tabs, dict):
        raise ScenarioError("sheet.tabs must be a table of tab name -> rows")
    for name, rows in tabs.items():
        if not (isinstance(rows, list) and all(isinstance(r, list) for r in rows)):
            raise ScenarioError(f"sheet.tabs.{name} must be a list of rows (lists of strings)")
        if not all(isinstance(c, (str, int, float, bool)) for r in rows for c in r):
            raise ScenarioError(f"sheet.tabs.{name} cells must be strings, numbers or booleans")
    return {name: [[str(c) for c in r] for r in rows] for name, rows in tabs.items()}


def _checks(raw, where, key, roots):
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not (isinstance(raw, list) and all(isinstance(e, str) for e in raw)):
        raise ScenarioError(f"{where}: {key} must be a list of expressions")
    for text in raw:
        try:
            expect.compile_expr(text, roots)
        except expect.ExpectError as exc:
            raise ScenarioError(f"{where}: {key} {text!r}: {exc}") from None
    return list(raw)


def _shot(raw, where):
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict) or set(raw) - {"name", "role", "element"}:
        raise ScenarioError(f"{where}: shot must be a name or a table with name, role, element")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ScenarioError(f"{where}: shot name must match {NAME_RE.pattern}")
    return Shot(name, str(raw.get("role") or ""), str(raw.get("element") or ""))


def _step(i, raw, registry, roots):
    where = f"step {i + 1}"
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where} must be a table")
    unknown = set(raw) - STEP_KEYS
    if unknown:
        raise ScenarioError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    role, do = raw.get("role"), raw.get("do")
    if not isinstance(role, str) or not role:
        raise ScenarioError(f"{where}: role is required")
    if not isinstance(do, str) or not do:
        raise ScenarioError(f"{where}: do is required")
    sid = raw.get("id", do)
    if not isinstance(sid, str) or not NAME_RE.match(sid):
        raise ScenarioError(f"{where}: id {sid!r} must match {NAME_RE.pattern}")
    where = f"step {i + 1} ({sid})"
    cls = registry.get(role_kind(role))
    if cls is None:
        raise ScenarioError(f"{where}: unknown role {role!r} (known: {', '.join(sorted(registry))})")
    if do not in cls.action_names():
        raise ScenarioError(f"{where}: role {role_kind(role)!r} has no action {do!r} "
                            f"(has: {', '.join(cls.action_names())})")
    args = raw.get("args", {})
    if not isinstance(args, dict):
        raise ScenarioError(f"{where}: args must be a table")
    timeout = parse_duration(raw["timeout"]) if "timeout" in raw else DEFAULT_CHECK_TIMEOUT
    wait = parse_duration(raw["wait"]) if "wait" in raw else DEFAULT_TRIGGER_WAIT
    shot = _shot(raw.get("shot"), where)
    if shot and shot.role and role_kind(shot.role) not in registry:
        raise ScenarioError(f"{where}: shot role {shot.role!r} is unknown")
    try:
        at = parse_at(raw.get("at"))
    except ScenarioError as exc:
        raise ScenarioError(f"{where}: {exc}") from None
    return Step(i, sid, role, do, at, args, _checks(raw.get("expect"), where, "expect", roots),
                _checks(raw.get("soft"), where, "soft", roots), timeout, wait, shot)


def from_dict(data, registry, name="scenario", path="", roots=expect.ROOTS):
    if not isinstance(data, dict):
        raise ScenarioError("a scenario is a TOML table")
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise ScenarioError(f"unknown top-level key(s) {', '.join(sorted(unknown))}")
    name = data.get("name", name)
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ScenarioError(f"name {name!r} must match {NAME_RE.pattern}")
    event = data.get("event") or {}
    if not isinstance(event, dict):
        raise ScenarioError("event must be a table")
    raw_steps = data.get("step") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScenarioError("a scenario needs at least one [[step]]")
    steps, ids = [], {}
    for i, raw in enumerate(raw_steps):
        step = _step(i, raw, registry, roots)
        if step.id in ids:
            raise ScenarioError(f"{step.label}: id {step.id!r} is not unique "
                                f"(also step {ids[step.id] + 1}); give one of them an id")
        if step.at.kind == "after" and step.at.ref not in ids:
            raise ScenarioError(f"{step.label}: at refers to {step.at.ref!r}, which is not an "
                                "earlier step")
        ids[step.id] = i
        steps.append(step)
    tolerances = data.get("tolerances") or {}
    if not isinstance(tolerances, dict):
        raise ScenarioError("tolerances must be a table")
    slow = tolerances.get("state_s", 2.0)
    if not isinstance(slow, (int, float)) or slow < 0:
        raise ScenarioError("tolerances.state_s must be a non-negative number")
    timeout = parse_duration(data["timeout"]) if "timeout" in data else DEFAULT_RUN_TIMEOUT
    return Scenario(name, str(data.get("description") or ""), timeout, event,
                    _commentators(event), _tabs(data.get("sheet")), {"state_s": float(slow)},
                    steps, path)


def load(path, registry):
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    stem = os.path.splitext(os.path.basename(path))[0]
    return from_dict(data, registry, name=stem, path=os.path.abspath(path))


def find(name_or_path, directory):
    """A bundled scenario by name, or a path to a TOML file."""
    if os.path.isfile(name_or_path):
        return name_or_path
    if not NAME_RE.match(name_or_path):
        raise ScenarioError(f"no scenario {name_or_path!r}")
    path = os.path.join(directory, name_or_path + ".toml")
    if not os.path.isfile(path):
        known = sorted(f[:-5] for f in os.listdir(directory) if f.endswith(".toml"))
        raise ScenarioError(f"no scenario {name_or_path!r} (bundled: {', '.join(known)})")
    return path

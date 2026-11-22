"""Role drivers: one class per party of the event (producer, director, commentator, ...).

A step's `role` is `kind` or `kind:instance` ("commentator:Comms 2"); the engine opens
one instance per distinct role string on its first step and closes them all at the end.
An action `do = "x"` calls the method `act_x(args)` of the role's class; its return
value (a dict, or None) becomes the step's result, which later steps read as
`${step_id.key}` in their args. A role signals a hard failure by raising `StepError`;
`ctx.finding("soft", text)` records a soft one.

`ctx` is the run context (`engine.RunContext`): service URLs, console tokens, the stream
stub, the simulated OBS and a shared headless browser (None without Playwright)."""


class StepError(Exception):
    """A hard failure of an action: an HTTP error, a missing or disabled control."""


class ShotUnavailable(Exception):  # noqa: N818  a soft outcome, not a bug
    """This role cannot take the screenshot; the engine records a soft finding."""


class Role:
    kind = ""

    def __init__(self, ctx, name):
        self.ctx = ctx
        self.name = name
        self.instance = name.split(":", 1)[1] if ":" in name else ""

    @classmethod
    def action_names(cls):
        return sorted(n[4:] for n in dir(cls) if n.startswith("act_") and callable(getattr(cls, n)))

    def open(self):
        """Called before the role's first action."""

    def do(self, action, args):
        return getattr(self, "act_" + action)(dict(args))

    def page(self):
        """Facts about the role's surface for `page(role)` checks."""
        return {}

    def shot(self, path, element=""):
        """Write a PNG of the role's surface (or `element` of it) to `path`."""
        raise ShotUnavailable(f"role {self.name!r} takes no screenshots")

    def close(self):
        """Release the role's pages and connections; returns a list of (severity, text)."""
        return []


REGISTRY = {}


def register(cls):
    if not cls.kind or cls.kind in REGISTRY:
        raise ValueError(f"role kind {cls.kind!r} is empty or taken")
    REGISTRY[cls.kind] = cls
    return cls


def default_registry():
    from roles import builtin  # noqa: F401  registers the built-in roles
    return dict(REGISTRY)

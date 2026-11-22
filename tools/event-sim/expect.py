"""The `expect` expression language: a restricted Python expression over named probes.

Allowed: literals, lists and tuples, `and`/`or`/`not`, comparisons (`==`, `!=`, `<`,
`<=`, `>`, `>=`, `in`, `not in`), unary minus, attribute and index access, and calls of
the methods a probe lists in `calls` (plus `len`). Never `eval`: anything else, a name
that is no probe, or an attribute starting with `_` is refused when the scenario loads.

A value that cannot be read yet (a missing key, a probe whose service is down) raises
`Unresolved`, so a check stays "not true yet" until its timeout."""
import ast

_BOOL = {ast.And: all, ast.Or: any}
_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
          ast.Compare, ast.Name, ast.Load, ast.Constant, ast.Attribute, ast.Subscript,
          ast.Call, ast.List, ast.Tuple, *_CMP)
BUILTINS = {"len": len}
ROOTS = ("obs", "relay", "sheet", "webhook", "race", "page")
MAX_LENGTH = 500


class ExpectError(ValueError):
    """An expression the language does not allow."""


class Unresolved(Exception):  # noqa: N818  a state, not a bug
    """A value that cannot be read yet."""


class Probe:
    """A named root of the language. `fields` are readable attributes, `calls` callable
    methods; `resolve` may be overridden for dynamic attributes."""
    fields = ()
    calls = ()

    def resolve(self, name):
        if name in self.fields or name in self.calls:
            return getattr(self, name)
        raise Unresolved(f"{type(self).__name__} has no {name!r}")


class Func:
    """A callable root such as `page(role)`."""

    def __init__(self, fn):
        self.fn = fn


def _key(name):
    return name.lower().replace(" ", "_")


class View:
    """Read-only attribute and index access into JSON data. Keys match without case,
    with spaces as underscores: `row.race_control` reads the key "Race Control"."""

    def __init__(self, data):
        self.data = data

    def resolve(self, name):
        if isinstance(self.data, dict):
            if name in self.data:
                return wrap(self.data[name])
            for k, v in self.data.items():
                if isinstance(k, str) and _key(k) == _key(name):
                    return wrap(v)
        raise Unresolved(f"no {name!r} in {short(self.data)}")

    def index(self, key):
        try:
            return wrap(self.data[key])
        except (KeyError, IndexError, TypeError):
            raise Unresolved(f"no [{key!r}] in {short(self.data)}") from None

    def __len__(self):
        return len(self.data)

    def __eq__(self, other):
        return self.data == raw(other)

    def __hash__(self):
        return id(self)

    def __contains__(self, item):
        return raw(item) in self.data

    def __repr__(self):
        return short(self.data)


def wrap(value):
    return View(value) if isinstance(value, (dict, list)) else value


def raw(value):
    return value.data if isinstance(value, View) else value


def short(value, limit=160):
    text = repr(raw(value))
    return text if len(text) <= limit else text[:limit - 3] + "..."


def compile_expr(text, roots=None):
    """The checked syntax tree of `text`; raises ExpectError for anything not allowed.
    With `roots`, a name that is neither a root nor `len` is refused too."""
    if not isinstance(text, str) or not text.strip():
        raise ExpectError("empty expression")
    if len(text) > MAX_LENGTH:
        raise ExpectError(f"longer than {MAX_LENGTH} characters")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpectError(f"syntax error: {exc.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise ExpectError(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExpectError(f"attribute {node.attr!r} is not allowed")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or (
                roots is not None and node.id not in roots and node.id not in BUILTINS)):
            raise ExpectError(f"unknown name {node.id!r}" + (f" (known: {', '.join(roots)})" if roots else ""))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise ExpectError("only probe methods, page() and len() can be called")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (str, int, float, bool, type(None))):
            raise ExpectError(f"constant {node.value!r} is not allowed")
    return tree


def names_in(tree):
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def evaluate(tree, names):
    """The value of a compiled expression with `names` as its roots."""
    return _eval(tree.body, names)


def explain(tree, names):
    """What a false check saw: the left side of a top-level comparison, else the value."""
    node = tree.body
    try:
        if isinstance(node, ast.Compare):
            return f"left side {short(_eval(node.left, names))}"
        return f"value {short(_eval(node, names))}"
    except Exception as exc:  # explaining must not fail the check twice
        return str(exc)


def truth(text, names):
    """(ok, value) for `text`; Unresolved propagates."""
    value = raw(evaluate(compile_expr(text), names))
    return bool(value), value


def _attr(obj, name):
    if isinstance(obj, (Probe, View)):
        return obj.resolve(name)
    raise Unresolved(f"{short(obj)} has no {name!r}")


def _eval(node, names):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        if node.id in BUILTINS:
            return BUILTINS[node.id]
        raise Unresolved(f"unknown name {node.id!r}")
    if isinstance(node, (ast.List, ast.Tuple)):
        return [raw(_eval(e, names)) for e in node.elts]
    if isinstance(node, ast.BoolOp):
        combine = _BOOL[type(node.op)]
        return combine(bool(raw(_eval(v, names))) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        value = raw(_eval(node.operand, names))
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return -value
        raise Unresolved(f"cannot negate {short(value)}")
    if isinstance(node, ast.Compare):
        left = raw(_eval(node.left, names))
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = raw(_eval(comp, names))
            try:
                if not _CMP[type(op)](left, right):
                    return False
            except TypeError as exc:
                raise Unresolved(f"cannot compare {short(left)} and {short(right)}: {exc}") from None
            left = right
        return True
    if isinstance(node, ast.Attribute):
        return _attr(_eval(node.value, names), node.attr)
    if isinstance(node, ast.Subscript):
        obj = _eval(node.value, names)
        key = raw(_eval(node.slice, names))
        if isinstance(obj, View):
            return obj.index(key)
        raise Unresolved(f"{short(obj)} cannot be indexed")
    if isinstance(node, ast.Call):
        args = [raw(_eval(a, names)) for a in node.args]
        if isinstance(node.func, ast.Attribute):
            owner = _eval(node.func.value, names)
            if not (isinstance(owner, Probe) and node.func.attr in owner.calls):
                raise Unresolved(f"{node.func.attr}() cannot be called here")
            fn = getattr(owner, node.func.attr)
        else:
            fn = _eval(node.func, names)
            if isinstance(fn, Func):
                fn = fn.fn
            elif fn is not len:
                raise Unresolved(f"{node.func.id}() cannot be called")
        try:
            return wrap(fn(*args))
        except TypeError as exc:
            raise Unresolved(f"{exc}") from None
    raise Unresolved(f"{type(node).__name__} cannot be evaluated")

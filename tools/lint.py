#!/usr/bin/env python3
"""Run the ruff linter over the repo (config: ruff.toml at the repo root), plus two
in-house guards that mirror CodeQL classes ruff has no rule for.

    python3 tools/lint.py          # check only (what CI runs)
    python3 tools/lint.py --fix    # auto-fix what ruff can (e.g. unused imports)

Ruff is a single external binary, not vendored. Install it once:
  macOS:  brew install ruff      Windows:  winget install astral-sh.ruff
  Linux:  pipx/pip install ruff (or the distro package)
The rule set mirrors the GitHub code-scanning (CodeQL) classes; see ruff.toml.

The empty-except guard exists because ruff's S110 stays OFF: it would re-flag the
documented `except ...: pass  # reason` blocks CodeQL accepts. This guard flags an
empty handler (`pass`/`...`) with NO explanatory comment, except the benign idioms
CodeQL also ignores: a deliberate `raise` in the try, or the optional-import idiom.
A missing comment then fails the gate pre-push instead of post-merge.

KeyboardInterrupt is NOT blanket-exempt. CodeQL flags a silent Ctrl-C swallow when
the protected try can also exit normally (#217), so the gate requires a comment on
it like any other swallow. That is at worst stricter than CodeQL, never laxer.

The procedure-return-value guard (find_proc_return_value_uses) reproduces CodeQL's
py/procedure-return-value-used: flag `x = proc()` / `return proc()` where the callee
only ever returns None (a 'procedure'). It is scoped to same-file, bare-name calls,
the recurring dispatcher shape, so it stays free of false positives; cross-module
and method cases stay CodeQL's job.
"""
import ast, io, os, shutil, subprocess, sys, tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Caught types for which a silent `pass` is an accepted idiom CodeQL does not flag:
# optional imports and interpreter/iterator-control signals. KeyboardInterrupt is
# deliberately NOT here; see the module docstring. (#217)
_BENIGN_EXC = frozenset({"ImportError", "ModuleNotFoundError",
                         "SystemExit", "StopIteration", "GeneratorExit"})


def _comment_lines(source):
    """1-based line numbers carrying a `#` comment (best-effort)."""
    lines = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass  # tokenizer gave up; the ast.parse below is the authority
    return lines


def _caught_names(handler):
    """The simple names of the exception types a handler catches (empty = bare)."""
    typ = handler.type
    if typ is None:
        return set()
    items = typ.elts if isinstance(typ, ast.Tuple) else [typ]
    out = set()
    for it in items:
        if isinstance(it, ast.Name):
            out.add(it.id)
        elif isinstance(it, ast.Attribute):
            out.add(it.attr)
    return out


def find_empty_excepts(source):
    """Line numbers of `except` handlers that silently swallow: the body is only
    `pass`/`...` with no comment in the handler. Excludes the idioms CodeQL
    py/empty-except also ignores, a deliberate `raise` anywhere in the try body
    (e.g. assert-raises tests) and handlers catching only _BENIGN_EXC types.
    Returns [] for unparseable source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    comments = _comment_lines(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        try_raises = any(isinstance(n, ast.Raise)
                         for s in node.body for n in ast.walk(s))
        for h in node.handlers:
            if len(h.body) != 1:
                continue
            stmt = h.body[0]
            empty = isinstance(stmt, ast.Pass) or (
                isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is Ellipsis)
            if not empty:
                continue
            end = getattr(stmt, "end_lineno", stmt.lineno)
            if any(h.lineno <= c <= end for c in comments):
                continue                       # has an explanatory comment
            if try_raises:
                continue                       # deliberate raise in try (assert-raises idiom)
            names = _caught_names(h)
            if names and names <= _BENIGN_EXC:
                continue
            hits.append(h.lineno)
    return hits


def _scope_returns_yields(fn):
    """The Return nodes and whether a yield appears in *fn*'s OWN scope (a nested
    def/lambda owns its own returns, so we stop at those)."""
    rets, has_yield, stack = [], False, list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue                       # nested scope, not ours
        if isinstance(n, ast.Return):
            rets.append(n)
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            has_yield = True
        stack.extend(ast.iter_child_nodes(n))
    return rets, has_yield


def _always_terminates(stmts):
    """True iff this statement block can NOT fall through its end: it always
    raises, returns, or sys.exit()s. It matches the trailing-raise and
    both-branches-raise shapes CodeQL excludes; anything more exotic counts as
    falling through. Used only for the no-`return` case below."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Raise, ast.Return)):
        return True
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        f = last.value.func                # sys.exit(...) / exit(...)
        if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) == "exit":
            return True
    if isinstance(last, ast.If) and last.body and last.orelse:
        return _always_terminates(last.body) and _always_terminates(last.orelse)
    return False


def _is_procedure(fn):
    """True iff *fn* is a 'procedure' in CodeQL's sense (py/procedure-return-value-
    used): it only ever returns None implicitly, via a bare `return` or by falling
    off the end. A `return <expr>` disqualifies it, including an explicit
    `return None`, which CodeQL treats as a deliberate value; so does a yield. A
    function that always raises or exits is not a procedure either, since it never
    returns None."""
    rets, has_yield = _scope_returns_yields(fn)
    if has_yield:
        return False
    if any(r.value is not None for r in rets):
        return False                       # returns a value (incl. `return None`)
    if any(r.value is None for r in rets):
        return True                        # has a bare `return` -> implicit-None exit
    return not _always_terminates(fn.body)  # no returns -> procedure iff it can fall through


def find_proc_return_value_uses(source):
    """(lineno, name) for every call whose result is used but whose callee is a
    same-file procedure, CodeQL's py/procedure-return-value-used. Same-file,
    bare-name calls only, no cross-module or method resolution: that is the
    recurring `return helper(args)` / `x = helper()` shape, and anything deeper
    stays CodeQL's job. A call counts as 'used' unless it is a statement on its
    own. Returns [] for unparseable source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    procs = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_procedure(n)}
    standalone = {id(n.value) for n in ast.walk(tree)
                  if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)}
    hits = [(n.lineno, n.func.id) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in procs and id(n) not in standalone]
    return sorted(hits)


def _python_files(root):
    """Tracked .py files (git, so dist/runtime/incoming stay excluded); a plain
    walk is the fallback when git is unavailable."""
    try:
        out = subprocess.check_output(["git", "-C", root, "ls-files", "*.py"],
                                      text=True)
        return [os.path.join(root, p) for p in out.split() if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip = {"dist", "runtime", "incoming", ".git", "__pycache__", ".venv"}
        files = []
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            files += [os.path.join(dirpath, n) for n in names if n.endswith(".py")]
        return files


def check_empty_excepts(root):
    """(relpath, lineno) for every uncommented silent-swallow except in the repo."""
    bad = []
    for path in _python_files(root):
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        for ln in find_empty_excepts(src):
            bad.append((os.path.relpath(path, root), ln))
    return sorted(bad)


def check_proc_return_value_uses(root):
    """(relpath, lineno, name) for every use of a same-file procedure's return
    value in the repo (CodeQL py/procedure-return-value-used)."""
    bad = []
    for path in _python_files(root):
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        for lineno, name in find_proc_return_value_uses(src):
            bad.append((os.path.relpath(path, root), lineno, name))
    return sorted(bad)


def main():
    if not shutil.which("ruff"):
        sys.exit("lint: ruff not found on PATH.\n"
                 "  install: brew install ruff  (macOS) | winget install astral-sh.ruff"
                 "  (Windows) | pipx install ruff  (Linux)")
    rc = subprocess.call(["ruff", "check", ROOT] + sys.argv[1:])
    bad = check_empty_excepts(ROOT)
    if bad:
        print("\nempty-except (CodeQL py/empty-except): add a short reason in the "
              "handler body, e.g. `pass  # already gone`:")
        for relpath, lineno in bad:
            print(f"  {relpath}:{lineno}: except body is only pass/... with no comment")
        rc = rc or 1
    proc_bad = check_proc_return_value_uses(ROOT)
    if proc_bad:
        print("\nprocedure-return-value-used (CodeQL py/procedure-return-value-used): "
              "this callee only ever returns None; drop the assignment / `return`, or "
              "give the callee an explicit `return <value>`:")
        for relpath, lineno, name in proc_bad:
            print(f"  {relpath}:{lineno}: result of {name}() is used but it returns None")
        rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

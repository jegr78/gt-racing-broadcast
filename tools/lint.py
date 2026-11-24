#!/usr/bin/env python3
"""Run the ruff linter over the repo (config: ruff.toml at the repo root), plus five
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

The assert guard (find_assert_side_effects) reproduces py/side-effect-in-assert with
CodeQL's own name list, so a test that calls `.append`/`.close`/... inside an assert
fails here instead of on main after the merge. The import guard
(find_import_both_ways) does the same for py/import-and-import-from.

The unread-store guard (find_unused_last_assignments) covers the part of
py/unused-local-variable ruff F841 misses: a store nobody reads afterwards although
the name is read elsewhere in the function, typically a trailing `t = step(t)`,
and an unpacked tuple none of whose names is read afterwards.
"""
import ast, io, os, shutil, subprocess, sys, tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Caught types for which a silent `pass` is an accepted idiom CodeQL does not flag:
# optional imports and iterator-control signals. KeyboardInterrupt and SystemExit are
# deliberately NOT here: CodeQL flags a silent swallow of either. (#217)
_BENIGN_EXC = frozenset({"ImportError", "ModuleNotFoundError",
                         "StopIteration", "GeneratorExit"})


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


# CodeQL's py/side-effect-in-assert list, copied from SideEffectInAssert.ql.
_SIDE_EFFECT_NAMES = frozenset({"print", "write", "append", "pop", "remove", "discard",
                                "delete", "close", "open", "exit"})
_SUBPROCESS_CALLS = frozenset({"call", "check_call", "check_output"})


def _probable_side_effect(node):
    if isinstance(node, (ast.Yield, ast.YieldFrom)):
        return True
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in _SIDE_EFFECT_NAMES:
        return True
    return (isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_CALLS
            and isinstance(func.value, ast.Name) and func.value.id == "subprocess")


def find_assert_side_effects(source):
    """Line numbers of the `assert` statements CodeQL's py/side-effect-in-assert flags:
    one that contains a call of a listed name, a subprocess call or a yield. Such a
    call vanishes under `python -O`. Returns [] for unparseable source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return sorted(n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)
                  and any(_probable_side_effect(c) for c in ast.walk(n)))


def find_import_both_ways(source, local):
    """(lineno, name) for each of the repo's own modules (top-level name in `local`)
    that a file imports with both `import M` and `from M import x`, CodeQL's
    py/import-and-import-from; CodeQL leaves the standard library out because it has
    no source for it. Returns [] for unparseable source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    plain = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                plain.setdefault(a.name, n.lineno)
    froms = {n.module for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}
    return sorted((line, name) for name, line in plain.items()
                  if name in froms and name.split(".")[0] in local)


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
           ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_LOOPS = (ast.For, ast.AsyncFor, ast.While)


def _dead_stores_in(fn):
    """(lineno, name) for the stores in *fn*'s own scope that no later Load reads,
    skipping every shape where source order is not enough."""
    stores, loads, rebinds, nested_loads, skip = [], [], [], set(), set()

    def walk(node, loops):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPES):
                if hasattr(child, "name"):
                    rebinds.append((child.name, (child.lineno, child.col_offset)))
                nested_loads.update(n.id for n in ast.walk(child) if isinstance(n, ast.Name))
                continue
            if isinstance(child, (ast.Global, ast.Nonlocal)):
                skip.update(child.names)
            elif isinstance(child, ast.Call) and getattr(child.func, "id", None) in ("locals", "vars", "eval", "exec"):
                return None
            elif isinstance(child, ast.Name):
                pos = (child.lineno, child.col_offset)
                if isinstance(child.ctx, ast.Load):
                    loads.append((child.id, pos))
                else:
                    rebinds.append((child.id, pos))
            elif isinstance(child, (ast.Import, ast.ImportFrom, ast.ExceptHandler)):
                names = [(a.asname or a.name).split(".")[0] for a in getattr(child, "names", ())]
                names += [child.name] if isinstance(getattr(child, "name", None), str) else []
                rebinds.extend((n, (child.lineno, child.col_offset)) for n in names)
            elif isinstance(child, ast.Assign):
                stores.append((child, loops))
            inner = loops + (child,) if isinstance(child, _LOOPS) else loops
            if walk(child, inner) is None:
                return None
        return True

    if walk(fn, ()) is None:
        return []

    def unread(name, stmt, loops):
        # In a loop the store redefines itself, and CodeQL leaves a redefined store
        # to py/multiple-definition; so does a later store of the name.
        end = (stmt.end_lineno, stmt.end_col_offset)
        return not loops and not any(n == name and pos > end
                                     for n, pos in loads + rebinds)

    def tuples(node):
        if isinstance(node, (ast.Tuple, ast.List)):
            yield node
            for elt in node.elts:
                yield from tuples(elt)

    hits = []
    for stmt, loops in stores:
        target = stmt.targets[0]
        if len(stmt.targets) == 1 and isinstance(target, ast.Name):
            name = target.id
            if (not name.startswith("_") and name not in skip and name not in nested_loads
                    and any(n == name for n, _ in loads)   # never read: ruff F841 covers it
                    and unread(name, stmt, loops)):
                hits.append((target.lineno, name))
            continue
        for tup in (t for tgt in stmt.targets for t in tuples(tgt)):
            if any(isinstance(e, ast.Starred) for e in tup.elts):
                continue
            names = [e for e in tup.elts if isinstance(e, ast.Name)]
            # CodeQL flags a tuple's names only when none of them is used; `_` counts as used.
            if names and all(set(e.id) != {"_"} and e.id not in skip and e.id not in nested_loads
                             and unread(e.id, stmt, loops) for e in names):
                hits += [(e.lineno, e.id) for e in names]
    return hits


def find_unused_last_assignments(source):
    """(lineno, name) for each store in a function that nothing reads afterwards, the
    part of CodeQL's py/unused-local-variable that ruff F841 misses. A plain
    `name = value` counts when the name is read elsewhere in the function and does not
    start with `_`; F841 has the never-read case. In tuple unpacking, CodeQL's rule
    applies: each immediate tuple's names are flagged only when none of them is
    read later, and a bare `_` or a starred element exempts the tuple. Augmented
    and annotated stores are left out. Skipped everywhere: global/nonlocal
    names, names read in a nested scope, functions that call
    locals()/vars()/eval()/exec(), a store that a later store or del of the
    name redefines (CodeQL's py/multiple-definition), which includes every
    store inside a loop. Returns [] for unparseable source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            hits += _dead_stores_in(fn)
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


def check_assert_side_effects(root):
    """(relpath, lineno) for every assert with a probable side effect in the repo
    (CodeQL py/side-effect-in-assert)."""
    bad = []
    for path in _python_files(root):
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        bad += [(os.path.relpath(path, root), ln) for ln in find_assert_side_effects(src)]
    return sorted(bad)


_STRING_STARTS = {tokenize.STRING} | ({tokenize.FSTRING_START} if hasattr(tokenize, "FSTRING_START") else set())


def _string_parts(segment):
    """How many string literals the source of one expression joins."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO("(" + segment + ")").readline))
    except (tokenize.TokenError, SyntaxError):
        return 0
    return sum(1 for t in tokens if t.type in _STRING_STARTS)


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _parenthesized(source, node):
    """True when `node` sits in its own parentheses in `source`."""
    lines = source.splitlines(True)
    offset = sum(len(line) for line in lines[:node.lineno - 1]) + node.col_offset
    before = source[:offset].rstrip()
    return before.endswith("(")


def find_implicit_concat_in_lists(source):
    """Line numbers of the list elements CodeQL's py/implicit-string-concatenation-in-list
    flags: two or more adjacent plain string literals, not in parentheses of their own,
    in a list that holds another string. It reads like a missing comma. Returns [] for
    unparseable source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        strings = [e for e in node.elts if _is_str(e)]
        if len(strings) < 2:
            continue
        for elt in strings:
            segment = ast.get_source_segment(source, elt)
            if segment and _string_parts(segment) > 1 and not _parenthesized(source, elt):
                bad.append(elt.lineno)
    return sorted(bad)


def check_implicit_concat_in_lists(root):
    """(relpath, lineno) for every implicitly concatenated string in a collection literal
    in the repo (CodeQL py/implicit-string-concatenation-in-list)."""
    bad = []
    for path in _python_files(root):
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        bad += [(os.path.relpath(path, root), ln) for ln in find_implicit_concat_in_lists(src)]
    return sorted(bad)


def check_import_both_ways(root):
    """(relpath, lineno, name) for every module imported both ways in the repo
    (CodeQL py/import-and-import-from)."""
    files = _python_files(root)
    local = {os.path.basename(os.path.dirname(p)) if os.path.basename(p) == "__init__.py"
             else os.path.splitext(os.path.basename(p))[0] for p in files}
    bad = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        bad += [(os.path.relpath(path, root), ln, name) for ln, name in find_import_both_ways(src, local)]
    return sorted(bad)


def check_unused_last_assignments(root):
    """(relpath, lineno, name) for every unread last store in the repo (CodeQL
    py/unused-local-variable)."""
    bad = []
    for path in _python_files(root):
        try:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue
        bad += [(os.path.relpath(path, root), ln, name) for ln, name in find_unused_last_assignments(src)]
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
    assert_bad = check_assert_side_effects(ROOT)
    if assert_bad:
        print("\nside-effect-in-assert (CodeQL py/side-effect-in-assert): `python -O` drops "
              "the whole assert, so call first and assert on the result:")
        for relpath, lineno in assert_bad:
            print(f"  {relpath}:{lineno}: assert contains a call that may have side effects")
        rc = rc or 1
    both = check_import_both_ways(ROOT)
    if both:
        print("\nimport-and-import-from (CodeQL py/import-and-import-from): import each "
              "module one way only:")
        for relpath, lineno, name in both:
            print(f"  {relpath}:{lineno}: '{name}' is imported with both import and from-import")
        rc = rc or 1
    concat = check_implicit_concat_in_lists(ROOT)
    if concat:
        print("\nimplicit-string-concatenation-in-list (CodeQL "
              "py/implicit-string-concatenation-in-list): join the parts with an explicit + "
              "or build the string outside the list, so a missing comma cannot hide:")
        for relpath, lineno in concat:
            print(f"  {relpath}:{lineno}: adjacent strings joined inside a list")
        rc = rc or 1
    dead = check_unused_last_assignments(ROOT)
    if dead:
        print("\nunused-local-variable (CodeQL py/unused-local-variable): nothing reads "
              "this value; drop the assignment and keep the call, or use the value. In an "
              "unpacked tuple only a bare `_` is a placeholder, or read one of its names:")
        for relpath, lineno, name in dead:
            print(f"  {relpath}:{lineno}: the value stored in '{name}' is never read")
        rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

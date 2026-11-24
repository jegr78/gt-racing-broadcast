#!/usr/bin/env python3
"""Unit checks for the in-house guards in tools/lint.py, each mirroring one CodeQL
query so it fails the gate pre-push: py/empty-except, py/procedure-return-value-used,
py/side-effect-in-assert, py/import-and-import-from and py/unused-local-variable.
Run: python3 tests/test_lint.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("lintmod", os.path.join(ROOT, "tools", "lint.py"))
lint = importlib.util.module_from_spec(spec); spec.loader.exec_module(lint)

# The fixtures are source strings, not live code, so this file itself stays clean.
SWALLOW = "try:\n    f()\nexcept OSError:\n    pass\n"
SWALLOW_ELLIPSIS = "try:\n    f()\nexcept OSError:\n    ...\n"
COMMENT_INLINE = "try:\n    f()\nexcept OSError:\n    pass  # already gone\n"
COMMENT_LINE = "try:\n    f()\nexcept OSError:\n    # already gone\n    pass\n"
NON_EMPTY = "try:\n    f()\nexcept OSError:\n    log()\n"
RAISE_IN_TRY = "try:\n    f()\n    raise AssertionError\nexcept ValueError:\n    pass\n"
BENIGN_IMPORT = "try:\n    import x\nexcept ImportError:\n    pass\n"
# KeyboardInterrupt is not blanket-benign: CodeQL flags a silent Ctrl-C swallow when
# the try can also exit normally, so the gate requires an explanatory comment on it
# like any other swallow. (#217)
KBD_NO_COMMENT = "try:\n    loop()\nexcept KeyboardInterrupt:\n    pass\n"
KBD_COMMENT = "try:\n    loop()\nexcept KeyboardInterrupt:\n    pass  # Ctrl-C\n"
BARE_EXCEPT = "try:\n    f()\nexcept:\n    pass\n"
MIXED_BENIGN = "try:\n    f()\nexcept (ImportError, OSError):\n    pass\n"
DOTTED = "try:\n    f()\nexcept asyncio.TimeoutError:\n    pass\n"


def t_flags_uncommented_swallow():
    assert lint.find_empty_excepts(SWALLOW) == [3]          # the `except` line


def t_flags_uncommented_ellipsis_body():
    assert lint.find_empty_excepts(SWALLOW_ELLIPSIS) == [3]


def t_inline_comment_suppresses():
    assert lint.find_empty_excepts(COMMENT_INLINE) == []


def t_comment_line_in_body_suppresses():
    assert lint.find_empty_excepts(COMMENT_LINE) == []


def t_non_empty_handler_not_flagged():
    assert lint.find_empty_excepts(NON_EMPTY) == []


def t_raise_in_try_is_excluded():
    # the assert-raises test idiom, which CodeQL ignores, so the gate does too
    assert lint.find_empty_excepts(RAISE_IN_TRY) == []


def t_benign_caught_types_excluded():
    assert lint.find_empty_excepts(BENIGN_IMPORT) == []      # optional-import guard


def t_keyboardinterrupt_swallow_needs_comment():
    # CodeQL flags a comment-free Ctrl-C swallow, and the gate mirrors that. (#217)
    assert lint.find_empty_excepts(KBD_NO_COMMENT) == [3]
    assert lint.find_empty_excepts(KBD_COMMENT) == []


def t_systemexit_swallow_needs_comment():
    src = "try:\n    main()\nexcept SystemExit:\n    pass\n"
    assert lint.find_empty_excepts(src) == [3], "CodeQL flags a silent SystemExit swallow"
    assert lint.find_empty_excepts(src.replace("pass\n", "pass  # --help exits\n")) == []


def t_bare_except_is_flagged():
    assert lint.find_empty_excepts(BARE_EXCEPT) == [3]


def t_mixed_benign_and_real_is_flagged():
    assert lint.find_empty_excepts(MIXED_BENIGN) == [3], \
        "catching a real error type alongside a benign one is still a silent swallow"


def t_dotted_exception_name_uses_attr():
    assert lint.find_empty_excepts(DOTTED) == [3]            # asyncio.TimeoutError, no comment


def t_syntax_error_source_is_safe():
    assert lint.find_empty_excepts("def (:\n  pass\n") == []


def t_repo_is_clean():
    # The whole repo must already satisfy the guard. (#139, #142)
    assert lint.check_empty_excepts(ROOT) == [], lint.check_empty_excepts(ROOT)


# The procedure-return-value-used guard (CodeQL py/procedure-return-value-used).
# A procedure, which returns only None, whose result is used.
PROC_USED_RETURN = "def p():\n    print(1)\n\ndef c():\n    return p()\n"
PROC_USED_ASSIGN = "def p():\n    print(1)\n\ndef c():\n    x = p()\n    return x\n"
PROC_BARE_RETURN = "def p():\n    if a:\n        return\n    print(1)\n\nx = p()\n"
# A standalone call whose result is discarded is fine.
PROC_STANDALONE = "def p():\n    print(1)\n\ndef c():\n    p()\n"
# `return None` is a deliberate value, so not a procedure; CodeQL ignores it.
RETURNS_NONE = "def p():\n    return None\n\ndef c():\n    return p()\n"
# Returns a real value, so not a procedure.
RETURNS_VALUE = "def p():\n    return 5\n\ndef c():\n    x = p()\n"
# Always raises or exits, so it never returns None and is not a procedure.
ALWAYS_RAISES = "def p():\n    raise SystemExit(1)\n\ndef c():\n    return p()\n"
ALWAYS_EXITS = "import sys\ndef p():\n    sys.exit(1)\n\ndef c():\n    return p()\n"
# A generator is not a procedure.
GENERATOR = "def p():\n    yield 1\n\ndef c():\n    x = p()\n"


def t_proc_return_flags_used_return():
    assert lint.find_proc_return_value_uses(PROC_USED_RETURN) == [(5, "p")]


def t_proc_return_flags_used_assignment():
    assert lint.find_proc_return_value_uses(PROC_USED_ASSIGN) == [(5, "p")]


def t_proc_return_flags_bare_return_procedure():
    assert lint.find_proc_return_value_uses(PROC_BARE_RETURN) == [(6, "p")]


def t_proc_return_standalone_call_ok():
    assert lint.find_proc_return_value_uses(PROC_STANDALONE) == []


def t_proc_return_explicit_none_not_a_procedure():
    assert lint.find_proc_return_value_uses(RETURNS_NONE) == []


def t_proc_return_value_returner_not_a_procedure():
    assert lint.find_proc_return_value_uses(RETURNS_VALUE) == []


def t_proc_return_always_raising_not_a_procedure():
    assert lint.find_proc_return_value_uses(ALWAYS_RAISES) == []
    assert lint.find_proc_return_value_uses(ALWAYS_EXITS) == []


def t_proc_return_generator_not_a_procedure():
    assert lint.find_proc_return_value_uses(GENERATOR) == []


def t_proc_return_syntax_error_source_is_safe():
    assert lint.find_proc_return_value_uses("def (:\n  pass\n") == []


def t_proc_return_repo_is_clean():
    # The whole repo must already satisfy this guard. (#117, #118, #120)
    assert lint.check_proc_return_value_uses(ROOT) == [], lint.check_proc_return_value_uses(ROOT)


ASSERT_METHOD = "xs = []\nassert xs.pop() == 1\n"
ASSERT_IN_LAMBDA = "assert run(cb=lambda: seen.append(1)) == 4\n"
ASSERT_NAME = "assert print('x') is None\n"
ASSERT_SUBPROCESS = "import subprocess\nassert subprocess.call(['true']) == 0\n"
ASSERT_YIELD = "def g():\n    assert (yield) is None\n"
ASSERT_COMPREHENSION = "assert all(x for x in xs)\n"
ASSERT_PURE_CALL = "assert role.do('get') == {}\n"
ASSERT_MESSAGE_ONLY = "assert ok, xs.pop()\n"


def t_assert_side_effect_flags_codeqls_names():
    assert lint.find_assert_side_effects(ASSERT_METHOD) == [2], "a .pop() inside an assert"
    assert lint.find_assert_side_effects(ASSERT_IN_LAMBDA) == [1], "an .append in a lambda counts too"
    assert lint.find_assert_side_effects(ASSERT_NAME) == [1], "a bare print() call"
    assert lint.find_assert_side_effects(ASSERT_SUBPROCESS) == [2], "subprocess.call"
    assert lint.find_assert_side_effects(ASSERT_YIELD) == [2], "an explicit yield"
    assert lint.find_assert_side_effects(ASSERT_MESSAGE_ONLY) == [1], "the message is part of the assert"


def t_assert_side_effect_leaves_other_calls_alone():
    assert lint.find_assert_side_effects(ASSERT_PURE_CALL) == [], "a name CodeQL does not list"
    assert lint.find_assert_side_effects(ASSERT_COMPREHENSION) == [], "a comprehension has no explicit yield"
    assert lint.find_assert_side_effects("def (:\n") == [], "unparseable source is skipped"


def t_assert_side_effect_repo_is_clean():
    assert lint.check_assert_side_effects(ROOT) == [], lint.check_assert_side_effects(ROOT)


def t_import_both_ways_flags_a_module_imported_with_import_and_from():
    assert lint.find_import_both_ways("import roles\nfrom roles import builtin\n", {"roles"}) == [(1, "roles")]
    assert lint.find_import_both_ways("def f():\n    from sim import x\nimport sim\n", {"sim"}) == [(3, "sim")], \
        "an import inside a function counts for the whole module"


def t_import_both_ways_leaves_other_shapes_alone():
    assert lint.find_import_both_ways("import roles\nimport roles.builtin\n", {"roles"}) == [], "two plain imports"
    assert lint.find_import_both_ways("import roles.a\nfrom roles import b\n", {"roles"}) == [], "different module names"
    assert lint.find_import_both_ways("from . import x\nimport x\n", {"x"}) == [], "a relative import names no module"
    assert lint.find_import_both_ways("import os\nfrom os import sep\n", {"roles"}) == [], \
        "the standard library is out, as CodeQL has no source for it"
    assert lint.find_import_both_ways("import (\n", {"x"}) == [], "unparseable source is skipped"


def t_import_both_ways_repo_is_clean():
    assert lint.check_import_both_ways(ROOT) == [], lint.check_import_both_ways(ROOT)


def t_implicit_concat_flags_adjacent_strings_in_a_list():
    assert lint.find_implicit_concat_in_lists('xs = ["a" "b", "c"]\n') == [1], "a list element"
    assert lint.find_implicit_concat_in_lists('xs = [\n    "a",\n    "b"\n    "c",\n]\n') == [3], \
        "an element over two lines, reported at its first line"


def t_implicit_concat_leaves_what_codeql_leaves_alone():
    assert lint.find_implicit_concat_in_lists('xs = ["a" + "b", "c"]\n') == [], "an explicit +"
    assert lint.find_implicit_concat_in_lists('xs = [("a"\n       "b"), "c"]\n') == [], "its own parentheses"
    assert lint.find_implicit_concat_in_lists('xs = ["a" "b", 1]\n') == [], "no other string in the list"
    assert lint.find_implicit_concat_in_lists('xs = ["a" f"{b}", "c"]\n') == [], "an f-string is no plain string"
    assert lint.find_implicit_concat_in_lists('xs = "x", "a" "b"\n') == [], "a tuple"
    assert lint.find_implicit_concat_in_lists('f("a" "b", "c")\n') == [], "call arguments"
    assert lint.find_implicit_concat_in_lists('xs = ["a", "b"]\n') == [], "separate elements"
    assert lint.find_implicit_concat_in_lists('xs = [\n') == [], "unparseable source is skipped"


def t_implicit_concat_repo_is_clean():
    assert lint.check_implicit_concat_in_lists(ROOT) == [], lint.check_implicit_concat_in_lists(ROOT)


def _dead(src):
    return lint.find_unused_last_assignments(src)


def t_dead_assignment_flags_a_final_unread_store():
    src = "def t():\n    t = go(0)\n    check(t)\n    t = go(t)\n"
    assert _dead(src) == [(4, "t")], "the last store of a name that was read earlier"
    assert _dead("async def f():\n    x = 1\n    use(x)\n    x = 2\n") == [(4, "x")], "async def too"
    assert _dead("def f(c):\n    if c:\n        x = 1\n        use(x)\n    else:\n        x = 2\n") == [(6, "x")], \
        "a store in the other branch that nothing reads afterwards"
    assert _dead("def f(xs):\n    for v in xs:\n        use(v)\n    v = 0\n") == [(4, "v")], \
        "a store after the loop is outside it"
    assert _dead("def f(v):\n    match v:\n        case [y]:\n            use(y)\n            y = 0\n") == [(5, "y")], \
        "a store in a case body"


def t_dead_assignment_handles_try_blocks():
    assert _dead("def f():\n    x = 0\n    try:\n        x = g()\n    except OSError:\n        x = 1\n"
                 "    finally:\n        use(x)\n") == [], "the finally block reads the handler's store"
    assert _dead("def f():\n    try:\n        x = g()\n    except OSError:\n        return\n"
                 "    else:\n        use(x)\n") == [], "the else block reads the try body's store"
    assert _dead("def f():\n    x = 0\n    use(x)\n    try:\n        g()\n    except OSError:\n"
                 "        x = 1\n") == [(7, "x")], "a handler's store nothing reads"


def t_dead_assignment_leaves_reads_and_never_read_names_alone():
    assert _dead("def f():\n    x = 1\n    x = 2\n    use(x)\n") == [], "every store is followed by a read"
    assert _dead("def f():\n    x = 1\n") == [], "a never-read name is ruff F841's job"
    assert _dead("x = 1\nuse(x)\nx = 2\n") == [], "module level is not a local scope"
    assert _dead("class C:\n    x = 1\n    y = x\n    x = 2\n") == [], "a class body is not a local scope"
    assert _dead("def f():\n    x = 1\n    use(x)\n    x = x + 1\n    use(x)\n") == [], "read on a later line"
    assert _dead("def (:\n") == [], "unparseable source is skipped"


def t_dead_assignment_skips_conservative_cases():
    tail = "    use(x)\n    x = 2\n"
    assert _dead("def f():\n    _x = 1\n    use(_x)\n    _x = 2\n") == [], "an underscore name"
    assert _dead("def f():\n    global x\n" + tail) == [], "a global"
    assert _dead("def f():\n    nonlocal x\n" + tail) == [], "a nonlocal"
    assert _dead("def f():\n" + tail + "    return lambda: x\n") == [], "read in a lambda"
    assert _dead("def f():\n" + tail + "    def g():\n        return x\n") == [], "read in a nested def"
    assert _dead("def f(ys):\n" + tail + "    return [x for _ in ys]\n") == [], "read in a comprehension"
    assert _dead("def f():\n" + tail + "    del x\n") == [], "a del"
    assert _dead("def f():\n" + tail + "    return locals()\n") == [], "locals() reads every name"
    assert _dead("def f():\n    x = 1\n    use(x)\n    x += 2\n") == [], "an augmented assignment"
    assert _dead("def f():\n    x = 1\n    use(x)\n    x, y = 2, 3\n    use(y)\n") == [], "a tuple target"
    assert _dead("def f():\n    x: int = 1\n    use(x)\n    x: int = 2\n") == [], "an annotated assignment"


def t_dead_assignment_flags_a_tuple_whose_names_are_all_unread():
    assert _dead("def f():\n    race, (a, b), t = field()\n    use(race, t)\n") == [(2, "a"), (2, "b")], \
        "a nested tuple nothing reads, even if its names were never read at all"
    assert _dead("def f():\n    (code, body), _c, _s = take()\n    use(code, body)\n") == [(2, "_c"), (2, "_s")], \
        "CodeQL exempts only bare underscores in a tuple, not _-prefixed names"
    assert _dead("def f():\n    cars = a, b = mk()\n    use(cars)\n") == [(2, "a"), (2, "b")], "a chained target"
    assert _dead("def f(r):\n    _c, r.a, r.b = load()\n") == [(2, "_c")], "attribute elements are not names"


def t_dead_assignment_leaves_a_tuple_with_one_read_name_alone():
    assert _dead("def f():\n    ok, note, _notes = w()\n    use(ok, note)\n") == [], "one element is read"
    assert _dead("def f():\n    (code, _b), c = take()\n    use(code, c)\n") == [], "the inner tuple has a read name"
    assert _dead("def f(r):\n    _, r.a = load()\n") == [], "a bare underscore is the accepted placeholder"
    assert _dead("def f():\n    a, *rest = xs()\n") == [], "a starred element"
    assert _dead("def f(xs):\n    for v in xs:\n        use(a)\n        a, b = v\n") == [], "read by the next iteration"
    assert _dead("def f():\n    a, b = xs()\n    return lambda: a\n") == [], "read in a nested scope"


def t_dead_assignment_respects_loops():
    assert _dead("def f(xs):\n    x = 0\n    for v in xs:\n        use(x)\n        x = v\n") == [], \
        "the next iteration reads it"
    assert _dead("def f():\n    x = 0\n    while x < 3:\n        x = step()\n") == [], "the loop header reads it"
    assert _dead("def f(xs):\n    for v in xs:\n        while ok(v):\n            use(x)\n            x = v\n") == [], \
        "a read in the enclosing loop counts"
    assert _dead("def f(xs):\n    x = 0\n    use(x)\n    for v in xs:\n        x = v\n") == [], \
        "the loop repeats the store, a redefinition CodeQL leaves to py/multiple-definition"


def t_dead_assignment_leaves_a_redefined_store_alone():
    assert _dead("def f():\n    (c, b), _s = t()\n    use(c)\n    (c, b), _s = t()\n    use(c)\n") == [(4, "_s")], \
        "only the last _s is flagged, the later store redefines the first"
    assert _dead("def f():\n    x = 1\n    use(x)\n    x = 2\n    for x in xs:\n        pass\n") == [], \
        "a loop target redefines it"
    assert _dead("def f():\n    x = 1\n    use(x)\n    x = 2\n    import x\n") == [], "an import redefines it"
    assert _dead("def f():\n    x = 1\n    use(x)\n    x = 2\n    try:\n        g()\n    except OSError as x:\n"
                 "        pass\n") == [], "an except-as name redefines it"
    assert _dead("def f():\n    x = 1\n    use(x)\n    x = 2\n    def x():\n        pass\n") == [], \
        "a def redefines it"


def t_dead_assignment_repo_is_clean():
    assert lint.check_unused_last_assignments(ROOT) == [], lint.check_unused_last_assignments(ROOT)


if __name__ == "__main__":
    for n, fn in sorted(globals().items()):
        if n.startswith("t_") and callable(fn):
            fn(); print("ok", n)
    print("ALL PASS")

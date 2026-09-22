#!/usr/bin/env python3
"""Unit checks for the in-house guards in tools/lint.py: the empty-except guard,
which mirrors CodeQL's py/empty-except, and the procedure-return-value guard, which
mirrors py/procedure-return-value-used. Both fail the gate pre-push.
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


def t_bare_except_is_flagged():
    assert lint.find_empty_excepts(BARE_EXCEPT) == [3]


def t_mixed_benign_and_real_is_flagged():
    # catching a real error type alongside a benign one is still a silent swallow
    assert lint.find_empty_excepts(MIXED_BENIGN) == [3]


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


if __name__ == "__main__":
    for n, fn in sorted(globals().items()):
        if n.startswith("t_") and callable(fn):
            fn(); print("ok", n)
    print("ALL PASS")

#!/usr/bin/env python3
"""Checks for tools/run-tests.py. Run: python3 tests/test_run_tests.py"""
import importlib.util, os, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "run_tests", os.path.join(ROOT, "tools", "run-tests.py"))
rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)


def _write(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def t_default_timeout_is_ten_minutes():
    assert rt.FILE_TIMEOUT_SECONDS == 600, rt.FILE_TIMEOUT_SECONDS


def t_hanging_file_fails_and_is_named():
    with tempfile.TemporaryDirectory() as d:
        ok = _write(d, "test_ok.py", "pass\n")
        slow = _write(d, "test_slow.py", "import time\ntime.sleep(60)\n")
        try:
            rt.main([ok, slow], timeout=5)
        except SystemExit as exc:
            message = str(exc.code)
        else:
            raise AssertionError("a file over the timeout must fail the run")
    assert "test_slow.py" in message and "timed out" in message, message
    assert "test_ok.py" not in message, message


def t_failing_file_is_named():
    with tempfile.TemporaryDirectory() as d:
        bad = _write(d, "test_bad.py", "raise SystemExit(1)\n")
        try:
            rt.main([bad], timeout=30)
        except SystemExit as exc:
            message = str(exc.code)
        else:
            raise AssertionError("a failing file must fail the run")
    assert "test_bad.py" in message, message


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

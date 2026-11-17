#!/usr/bin/env python3
"""Runs the timing host Worker's deno tests (#1161). Skips without deno unless
RACECAST_REQUIRE_DENO=1 (CI sets it). Run: python3 tests/test_timing_worker.py"""
import os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def t_worker_deno_tests_pass():
    deno = shutil.which("deno")
    if not deno:
        assert os.environ.get("RACECAST_REQUIRE_DENO") != "1", "deno is required here but missing"
        print("skip: deno not installed")
        return
    r = subprocess.run([deno, "test", "--no-check", "--no-prompt", "tests/timing-host/"],
                       cwd=ROOT, capture_output=True, text=True, errors="replace", timeout=180,
                       env=dict(os.environ, NO_COLOR="1"))
    assert r.returncode == 0, f"deno tests failed:\n{r.stdout[-4000:]}\n{r.stderr[-2000:]}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

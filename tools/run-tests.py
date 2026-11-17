#!/usr/bin/env python3
"""Run every tests/test_*.py as its own process, the project's no-pytest
convention, and exit non-zero if any fails or exceeds its time limit. This is
exactly what CI runs."""
import glob, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE_TIMEOUT_SECONDS = 600


def main(paths=None, timeout=FILE_TIMEOUT_SECONDS):
    if paths is None:
        paths = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    failures = []
    for path in paths:
        name = os.path.basename(path)
        print(f"== {name}", flush=True)
        try:
            rc = subprocess.call([sys.executable, path], timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"== {name} timed out after {timeout}s", flush=True)
            failures.append(f"{name} (timed out after {timeout}s)")
            continue
        if rc != 0:
            failures.append(name)
    if failures:
        sys.exit("FAILED: " + ", ".join(failures))
    print("ALL TEST FILES PASS")


if __name__ == "__main__":
    main()

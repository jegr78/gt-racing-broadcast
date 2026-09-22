#!/usr/bin/env python3
"""Run every tests/test_*.py as its own process, the project's no-pytest
convention, and exit non-zero if any fails. This is exactly what CI runs."""
import glob, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    failures = []
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py"))):
        print(f"== {os.path.basename(path)}", flush=True)
        if subprocess.call([sys.executable, path]) != 0:
            failures.append(os.path.basename(path))
    if failures:
        sys.exit("FAILED: " + ", ".join(failures))
    print("ALL TEST FILES PASS")


if __name__ == "__main__":
    main()

"""Unit tests for the shared build-version helper (src/scripts/app_version.py).
Stdlib only, runnable as a script. Mirrors racecast.version()."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "scripts"))
import app_version as av  # noqa: E402


def t_reads_trimmed_version():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "VERSION"), "w", encoding="utf-8") as fh:
            fh.write("1.2.3\n")
        assert av.read_version(d) == "1.2.3"


def t_whitespace_only_is_dev():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "VERSION"), "w", encoding="utf-8") as fh:
            fh.write("   \n")
        assert av.read_version(d) == "dev"


def t_missing_file_is_dev():
    with tempfile.TemporaryDirectory() as d:
        assert av.read_version(d) == "dev"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("all app_version tests passed")

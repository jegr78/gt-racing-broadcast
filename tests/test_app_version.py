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


def t_label_has_exactly_one_v():
    # The release workflow stamps the tag (`v1.13.0`); the badge once read "vv1.13.0".
    assert av.label("v1.13.0") == "v1.13.0"
    assert av.label("1.13.0") == "v1.13.0"
    assert av.label("dev") == "dev"


def t_relay_badge_uses_the_label():
    relay = os.path.join(os.path.dirname(__file__), "..", "src", "relay", "racecast-feeds.py")
    with open(relay, encoding="utf-8") as fh:
        src = fh.read()
    assert "VERSION_LABEL = app_version.label(APP_VERSION)" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("all app_version tests passed")

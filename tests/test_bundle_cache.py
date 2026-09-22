#!/usr/bin/env python3
"""Unit tests for src/scripts/bundle_cache.py (stdlib, no pytest).

A PyInstaller onefile process extracts src/ into the OS temp dir, and the OS
reaps that dir while the process still runs (macOS dirhelper after 3 days,
systemd-tmpfiles after 10). Files read per request then vanish under a
long-running Control Center.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "scripts"))
import bundle_cache as bc


def _tmpfile(body=b"<html>hi</html>"):
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    return path


def t_read_returns_file_bytes():
    path = _tmpfile()
    try:
        assert bc.BundleCache().read(path) == b"<html>hi</html>"
    finally:
        os.unlink(path)


def t_second_read_survives_the_file_being_deleted():
    # The OS reaped the extraction dir; the cache keeps serving.
    path = _tmpfile(b"PAGE")
    cache = bc.BundleCache()
    assert cache.read(path) == b"PAGE"
    os.unlink(path)
    assert cache.read(path) == b"PAGE"


def t_read_without_a_cached_copy_raises_oserror():
    cache = bc.BundleCache()
    try:
        cache.read(os.path.join(tempfile.gettempdir(), "racecast-not-here.html"))
    except OSError:
        pass  # nothing cached and nothing on disk, so the caller must handle it
    else:
        raise AssertionError("a missing, never-read file must raise")


def t_cached_copy_wins_over_a_later_changed_file():
    # Bundle files are immutable for the life of the process. Anything that must
    # pick up edits (profile overlay CSS!) must NOT go through this cache.
    path = _tmpfile(b"first")
    try:
        cache = bc.BundleCache()
        assert cache.read(path) == b"first"
        with open(path, "wb") as fh:
            fh.write(b"second")
        assert cache.read(path) == b"first"
    finally:
        os.unlink(path)


def t_prewarm_loads_everything_it_can():
    a, b = _tmpfile(b"A"), _tmpfile(b"B")
    missing = os.path.join(tempfile.gettempdir(), "racecast-absent.bin")
    try:
        cache = bc.BundleCache()
        loaded = cache.prewarm([a, b, missing])
        assert loaded == 2, loaded
        os.unlink(a)
        assert cache.read(a) == b"A"      # survived deletion thanks to prewarm
    finally:
        for p in (a, b):
            if os.path.exists(p):
                os.unlink(p)


def t_prewarm_never_raises_on_a_missing_file():
    # Called at startup; a kit/build without one optional page must not crash.
    assert bc.BundleCache().prewarm(["/definitely/not/here"]) == 0


def t_cached_reports_what_is_held():
    path = _tmpfile(b"X")
    try:
        cache = bc.BundleCache()
        assert not cache.cached(path)
        cache.read(path)
        assert cache.cached(path)
    finally:
        os.unlink(path)


def t_eviction_hint_names_the_cause_and_the_cure():
    msg = bc.eviction_hint("/tmp/_MEIabc/src/ui/app.html")
    lowered = msg.lower()
    assert "restart" in lowered, msg
    assert "temp" in lowered or "temporary" in lowered, msg


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("PASS test_bundle_cache")

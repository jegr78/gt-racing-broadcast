#!/usr/bin/env python3
"""Atomic replacement survives a concurrent reader (Windows WinError 5).

On Windows, os.replace() onto a file another process holds open fails with
PermissionError, because Python opens files without FILE_SHARE_DELETE. The relay
reads the asset manifests and audience files constantly, so every writer of those
files retries briefly, with the helper #877 introduced for the context files.
Run: python3 tests/test_atomic_replace.py"""

import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "scripts"))
import asset_metadata  # noqa: E402
import atomic_replace  # noqa: E402
import audience_connections  # noqa: E402
import audience_widgets  # noqa: E402

REAL_REPLACE = os.replace


def _conflict(winerror=5):
    """The PermissionError Windows raises while a reader holds the target open."""
    error = PermissionError(13, "fixture: target is open in another process")
    error.winerror = winerror
    return error


def _flaky_replace(failures, winerror=5):
    """os.replace stand-in that raises a Windows conflict `failures` times first."""
    calls = []

    def fake(source, target):
        calls.append((source, target))
        if len(calls) <= failures:
            raise _conflict(winerror)
        return REAL_REPLACE(source, target)

    return fake, calls


def _windows(fake, clock=None, sleeps=None):
    """Patch the helper onto a simulated Windows with a fake clock and sleep."""
    clock = clock if clock is not None else [0.0]
    sleeps = sleeps if sleeps is not None else []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    return (patch.object(atomic_replace.os, "replace", fake),
            patch.object(atomic_replace.os, "name", "nt"),
            patch.object(atomic_replace.time, "monotonic", lambda: clock[0]),
            patch.object(atomic_replace.time, "sleep", sleep))


def t_helper_retries_a_windows_conflict_then_succeeds():
    with tempfile.TemporaryDirectory() as td:
        source, target = Path(td) / "a.tmp", Path(td) / "a.json"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        for code in (5, 32, 33):  # access denied, sharing and lock violation
            source.write_text("new", encoding="utf-8")
            fake, calls = _flaky_replace(3, code)
            p1, p2, p3, p4 = _windows(fake)
            with p1, p2, p3, p4:
                try:
                    atomic_replace.replace(source, target)
                except PermissionError:
                    raise AssertionError(f"WinError {code} was not retried") from None
            assert len(calls) == 4, (code, calls)
            assert target.read_text(encoding="utf-8") == "new"
            assert not source.exists()


def t_helper_gives_up_at_the_deadline():
    with tempfile.TemporaryDirectory() as td:
        source, target = Path(td) / "a.tmp", Path(td) / "a.json"
        source.write_text("new", encoding="utf-8")
        fake, calls = _flaky_replace(10**6)
        sleeps = []
        p1, p2, p3, p4 = _windows(fake, sleeps=sleeps)
        with p1, p2, p3, p4:
            try:
                atomic_replace.replace(source, target)
            except PermissionError:
                pass  # expected: the error must surface
            else:
                raise AssertionError("a persistent conflict must surface")
        assert abs(sum(sleeps) - atomic_replace.DEADLINE_S) < 1e-9, sum(sleeps)
        assert max(sleeps) <= atomic_replace.MAX_DELAY_S, sleeps
        assert len(calls) == len(sleeps) + 1, (len(calls), len(sleeps))


def t_helper_does_not_retry_other_errors_or_platforms():
    for platform, code in (("nt", 2), ("nt", None), ("posix", 5)):
        calls = []

        def fake(source, target, calls=calls, code=code):
            calls.append(source)
            raise _conflict(code)

        with (patch.object(atomic_replace.os, "replace", fake),
              patch.object(atomic_replace.os, "name", platform),
              patch.object(atomic_replace.time, "sleep", lambda _s: None)):
            try:
                atomic_replace.replace("a", "b")
            except PermissionError:
                pass  # expected: the error must surface unretried
            else:
                raise AssertionError("the error must surface")
        assert len(calls) == 1, (platform, code, calls)


def t_gt7_context_shares_the_helper():
    # #877 fixed the same race for the Race Engineer/context files; one helper only.
    import gt7_context
    assert gt7_context._retry_windows_file_operation is         atomic_replace.retry_windows_file_operation


def _writers(directory):
    """(name, write-callable, target path) for every retrying writer."""
    directory = Path(directory)
    store = audience_widgets.Store(directory / "audience.json")
    return (
        ("asset_metadata.write",
         lambda: asset_metadata.write(directory, {"aliases": {"A": "B"}}),
         directory / "manifest.json"),
        ("audience_widgets.Store._write",
         lambda: store._write({"version": 1, "widgets": []}),
         directory / "audience.json"),
        ("audience_connections.write_atomic",
         lambda: audience_connections.write_atomic(
             directory / "connection.json", b"{}"),
         directory / "connection.json"),
    )


def t_writers_retry_a_transient_permission_error():
    for index in range(3):
        with tempfile.TemporaryDirectory() as td:
            name, write, target = _writers(td)[index]
            fake, calls = _flaky_replace(4)
            p1, p2, p3, p4 = _windows(fake)
            with p1, p2, p3, p4:
                write()
            assert len(calls) == 5, (name, calls)
            assert target.exists(), name
            leftovers = [p.name for p in Path(td).iterdir() if p.suffix == ".tmp"]
            assert not leftovers, (name, leftovers)


def t_writers_clean_temp_file_when_retries_run_out():
    for index in range(3):
        with tempfile.TemporaryDirectory() as td:
            name, write, target = _writers(td)[index]
            fake, _ = _flaky_replace(10**6)
            p1, p2, p3, p4 = _windows(fake)
            with p1, p2, p3, p4:
                try:
                    write()
                except PermissionError:
                    pass  # expected: the error must surface
                else:
                    raise AssertionError(name + " swallowed the failure")
            leftovers = [p.name for p in Path(td).iterdir() if p.suffix == ".tmp"]
            assert not leftovers, (name, leftovers)


def t_windows_real_open_handle_is_survived():
    """Windows only: a reader holding the target open must not fail the write."""
    if os.name != "nt":
        print("skip t_windows_real_open_handle_is_survived (Windows only)")
        return
    for index in range(3):
        with tempfile.TemporaryDirectory() as td:
            name, write, target = _writers(td)[index]
            write()  # create the target first
            handle = open(target, "rb")  # noqa: SIM115 - held open on purpose
            closer = threading.Timer(0.05, handle.close)
            closer.start()
            try:
                write()
            finally:
                closer.join()
                handle.close()
            assert target.exists(), name


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("ALL PASS")

#!/usr/bin/env python3
"""Atomic replacement survives a concurrent reader (Windows WinError 5).

On Windows, os.replace() onto a file another process holds open fails with
PermissionError, because Python opens files without FILE_SHARE_DELETE. The relay
reads the asset manifests and audience files constantly, so every writer of those
files retries briefly. Run: python3 tests/test_atomic_replace.py"""

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


def _flaky_replace(failures):
    """os.replace stand-in that raises PermissionError `failures` times first."""
    calls = []

    def fake(source, target):
        calls.append((source, target))
        if len(calls) <= failures:
            raise PermissionError(13, "fixture: target is open in another process")
        return REAL_REPLACE(source, target)

    return fake, calls


def _no_sleep(_seconds):
    return None


def t_helper_retries_permission_error_then_succeeds():
    with tempfile.TemporaryDirectory() as td:
        source, target = Path(td) / "a.tmp", Path(td) / "a.json"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        fake, calls = _flaky_replace(3)
        with patch.object(atomic_replace.os, "replace", fake):
            atomic_replace.replace(source, target, sleep=_no_sleep)
        assert len(calls) == 4, calls
        assert target.read_text(encoding="utf-8") == "new"
        assert not source.exists()


def t_helper_gives_up_after_bounded_attempts():
    with tempfile.TemporaryDirectory() as td:
        source, target = Path(td) / "a.tmp", Path(td) / "a.json"
        source.write_text("new", encoding="utf-8")
        fake, calls = _flaky_replace(1000)
        with patch.object(atomic_replace.os, "replace", fake):
            try:
                atomic_replace.replace(source, target, sleep=_no_sleep)
            except PermissionError:
                pass  # expected: the error must surface
            else:
                raise AssertionError("a persistent PermissionError must surface")
        assert len(calls) == atomic_replace.ATTEMPTS, len(calls)


def t_helper_total_wait_stays_well_under_a_second():
    waits = []
    with tempfile.TemporaryDirectory() as td:
        source, target = Path(td) / "a.tmp", Path(td) / "a.json"
        source.write_text("new", encoding="utf-8")
        fake, _ = _flaky_replace(1000)
        with patch.object(atomic_replace.os, "replace", fake):
            try:
                atomic_replace.replace(source, target, sleep=waits.append)
            except PermissionError:
                pass  # expected: the error must surface
    assert len(waits) == atomic_replace.ATTEMPTS - 1, waits
    assert 0 < sum(waits) < 0.6, sum(waits)


def t_helper_does_not_retry_other_errors():
    calls = []

    def fake(source, target):
        calls.append(source)
        raise FileNotFoundError(2, "fixture")

    with patch.object(atomic_replace.os, "replace", fake):
        try:
            atomic_replace.replace("a", "b", sleep=_no_sleep)
        except FileNotFoundError:
            pass  # expected: the error must surface
        else:
            raise AssertionError("FileNotFoundError must surface")
    assert len(calls) == 1, calls


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
            with (
                patch.object(atomic_replace.os, "replace", fake),
                patch.object(atomic_replace.time, "sleep", _no_sleep),
            ):
                write()
            assert len(calls) == 5, (name, calls)
            assert target.exists(), name
            leftovers = [p.name for p in Path(td).iterdir() if p.suffix == ".tmp"]
            assert not leftovers, (name, leftovers)


def t_writers_clean_temp_file_when_retries_run_out():
    for index in range(3):
        with tempfile.TemporaryDirectory() as td:
            name, write, target = _writers(td)[index]
            fake, _ = _flaky_replace(1000)
            with (
                patch.object(atomic_replace.os, "replace", fake),
                patch.object(atomic_replace.time, "sleep", _no_sleep),
            ):
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

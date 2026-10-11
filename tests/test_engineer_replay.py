#!/usr/bin/env python3
"""Maintainer replay uses private copies and real signed production HTTP."""

import importlib.util
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from test_gt7_laps import write_circle_recording

spec = importlib.util.spec_from_file_location(
    "engineer_replay", ROOT / "tools/engineer-replay.py"
)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def request(session, route, signed=True, method="GET"):
    token = "?t=" + session.token if signed else ""
    url = session.origin + route + token
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, method=method), timeout=5
        ) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as response:
        return response.code, json.load(response)


def t_private_copies_labelled_grants_and_route_boundaries():
    with tempfile.TemporaryDirectory() as d:
        live_dir, ref_dir = Path(d) / "live", Path(d) / "reference"
        live_dir.mkdir()
        ref_dir.mkdir()
        path = Path(write_circle_recording(str(live_dir), n=100))
        reference = Path(write_circle_recording(str(ref_dir), n=100))
        before, ref_before = path.read_bytes(), reference.read_bytes()
        with replay.ReplaySession(
            path, references=[(reference, "Morning practice")], speed=10000
        ) as session:
            temporary = session.runtime
            assert session.recording != path and not session.recording.is_symlink()
            assert session.recording.read_bytes() == before
            status, doc = request(session, "/console/race-engineer/access")
            assert status == 200 and doc["enabled"]
            assert doc["references"][0]["label"] == "Morning practice"
            status, doc = request(
                session, "/console/race-engineer/snapshot", signed=False
            )
            assert status == 401 and doc["error"]["code"] == "unauthorized"
            for route, method in [
                ("/obs/scene", "POST"),
                ("/next", "GET"),
                ("/status", "GET"),
            ]:
                status, doc = request(session, route, method=method)
                assert status == 404 and doc["error"]["code"] == "not_found"
            session.play()
            status, snap = request(session, "/console/race-engineer/snapshot")
            assert status == 200 and snap["current"]["state"] == "provisional"
            assert session.store.stats()["dropped"] == 0
            assert path.read_bytes() == before and reference.read_bytes() == ref_before
        assert not temporary.exists(), "replay leaked temporary input copies"


def t_finite_options_and_bad_recording_are_rejected():
    for options in (
        {"speed": 0},
        {"speed": -1},
        {"speed": float("inf")},
        {"skip_minutes": -1},
        {"skip_minutes": float("nan")},
        {"port": 65536},
    ):
        try:
            replay.ReplaySession("unused.gt7rec", **options)
        except ValueError:
            pass  # Invalid numeric replay options are expected to fail here.
        else:
            raise AssertionError("invalid replay option accepted")
    with tempfile.TemporaryDirectory() as d:
        source = Path(d) / "invalid.gt7rec"
        source.write_bytes(b"not a recording")
        try:
            with replay.ReplaySession(source):
                raise AssertionError("bad input was served")
        except (ValueError, replay.gt7_recording.RecordingError):
            pass


def t_pacing_speed_and_large_skipped_prefix_keep_recorded_time_without_drops():
    with tempfile.TemporaryDirectory() as d:
        path = Path(write_circle_recording(d, lap_secs=(20,) * 8, n=400))
        now = [1000.0]
        waits = []

        def wait(delay):
            waits.append(delay)
            now[0] += delay
            return False

        with replay.ReplaySession(
            path, speed=2, skip_minutes=2, clock=lambda: now[0], waiter=wait
        ) as session:
            import threading

            release = threading.Event()
            consume, flush = session.store._consume, session.flush

            def held_first_packet(item):
                assert release.wait(10)
                consume(item)

            def release_at_flush(on_batch=None):
                release.set()
                flush(on_batch)

            session.store._consume, session.flush = held_first_packet, release_at_flush
            seen = []
            try:
                session.play(lambda r: seen.append((r.packets, r.position_s, now[0])))
            finally:
                release.set()
            assert session.store.stats()["dropped"] == 0, (
                "bounded pre-feed dropped input under worker pressure"
            )
            assert abs(now[0] - 1000 - (session.position_s - 120) / 2) < 0.00001, (
                "speed/skip changed pacing deadline"
            )
            prefix = [
                n for n, position, stamp in seen if position < 120 and stamp == 1000
            ]
            assert prefix and max(prefix) > 2048, (
                "fixture did not exceed the bounded queue in its skipped prefix"
            )
            assert (
                waits and max(waits) <= 0.25 and session.store.stats()["dropped"] == 0
            )
            assert all(stamp == 1000 for _, position, stamp in seen if position < 120)
            assert session.store.snapshot()["current"]["elapsed_s"] > 0


def t_updates_are_explicit_temporary_and_invalid_cache_falls_back_offline():
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        source = Path(write_circle_recording(d, n=100))
        data = root / "cache"
        (data / "gt7").mkdir(parents=True)
        cached = data / "gt7/cars.csv"
        cached.write_bytes(b"invalid optional cache")
        with patch.object(
            replay.gt7_data, "update", side_effect=AssertionError("unexpected download")
        ):
            with replay.ReplaySession(source, data_runtime=data) as session:
                assert len(session.cars) >= replay.gt7_data.MIN_CARS
                assert len(session.tracks.layouts()) >= replay.gt7_data.MIN_LAYOUTS
        called = []

        def update(directory, force=False):
            called.append((Path(directory), force))
            return dict(files={})

        with patch.object(replay.gt7_data, "update", side_effect=update):
            with replay.ReplaySession(
                source, data_runtime=data, update_data=True
            ) as session:
                assert called == [(session.runtime, True)]
                assert cached.read_bytes() == b"invalid optional cache"


def t_cancellation_interrupts_pacing_and_closes_owned_threads():
    import time

    with tempfile.TemporaryDirectory() as d:
        source = Path(write_circle_recording(d, n=100))
        with replay.ReplaySession(source, speed=0.01) as session:
            session.start()
            started = time.monotonic()
            while session.packets == 0 and time.monotonic() - started < 2:
                time.sleep(0.01)
            assert session.packets > 0
            thread = session._play_thread
            store_worker, api_worker = session.store._worker, session.store.api._worker
            temporary = session.runtime
        assert time.monotonic() - started < 2, (
            "cancellation waited for the slow recording pace"
        )
        assert (
            not thread.is_alive()
            and not store_worker.is_alive()
            and not api_worker.is_alive()
        )
        assert not temporary.exists()


def t_foreign_extension_and_recorded_timestamp_errors_are_explicit():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        original = Path(write_circle_recording(d, n=100))
        foreign = root / "foreign.bin"
        foreign.write_bytes(original.read_bytes())
        try:
            with replay.ReplaySession(foreign):
                raise AssertionError("foreign extension was accepted")
        except ValueError as exc:
            assert "finalized .gt7rec" in str(exc)
        packet = next(replay.gt7_recording.Recording(str(original)).packets())[2]
        header = original.read_bytes().split(b"\n", 1)[0] + b"\n"
        for walls in ((2.0, 1.0), (float("nan"), 2.0)):
            bad = root / "bad-time.gt7rec"
            bad.write_bytes(
                header
                + b"".join(
                    replay.gt7_recording._encode(wall, ord("~"), packet)
                    for wall in walls
                )
            )
            try:
                with replay.ReplaySession(bad, speed=10000) as session:
                    session.play()
            except ValueError as exc:
                assert "timestamps must be finite and nondecreasing" in str(exc)
            else:
                raise AssertionError("invalid recorded timestamp accepted")

    def invalid_wait(_delay):
        raise AssertionError("nonfinite deadline reached a wait")

    session = replay.ReplaySession("unused.gt7rec", waiter=invalid_wait)
    try:
        session.wait_until(float("inf"))
    except ValueError as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("nonfinite deadline was accepted")


def t_setup_thread_failure_cleans_copies_and_started_analysis_workers():
    import subprocess

    with tempfile.TemporaryDirectory() as d:
        source = Path(write_circle_recording(d, n=100))
        script = """import sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/'tests'))
import test_engineer_replay as t
from unittest.mock import patch
tempfile.tempdir = sys.argv[2]
original = threading.Thread.start
def fail_http(thread):
    if getattr(thread._target, '__name__', '') == 'serve_forever':
        raise RuntimeError('simulated HTTP worker startup failure')
    return original(thread)
session = t.replay.ReplaySession(sys.argv[1])
with patch.object(threading.Thread, 'start', fail_http):
    try:
        with session:
            raise AssertionError('HTTP startup failure was ignored')
    except RuntimeError as exc:
        assert 'simulated HTTP worker' in str(exc)
assert not session.runtime.exists(), 'setup failure leaked private copies'
assert not session.store._worker.is_alive() and not session.store._reader.is_alive()
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, str(source), d],
                cwd=ROOT,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=10,
            )
        except subprocess.TimeoutExpired as exc:
            raise AssertionError("setup failure stranded replay resources") from exc
        assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("t_") and callable(fn):
            fn()
            print("PASS", name)

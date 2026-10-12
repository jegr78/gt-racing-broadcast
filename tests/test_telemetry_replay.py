"""Operator replay guards, pacing and source isolation using synthetic packets."""
import math
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gt7_telemetry
import telemetry_replay
import race_engineer_live
from test_gt7_laps import write_circle_recording, FakeTracks, FakeCars


def t_source_guards():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "telemetry-recordings"
        root.mkdir()
        path = root / "drive.gt7rec"
        Path(write_circle_recording(str(root))).rename(path)
        control = telemetry_replay.Controller(gt7_telemetry.TelemetryStore(), root)
        for name in ("../drive.gt7rec", "a/drive.gt7rec", "a\\drive.gt7rec", "drive.csv", "", "-drive.gt7rec"):
            try:
                control.start(name)
                raise AssertionError(name)
            except telemetry_replay.ReplayError as exc:
                assert exc.code == "invalid_recording"
        link = root / "link.gt7rec"
        try:
            link.symlink_to(path)
        except OSError:
            pass
        else:
            try:
                control.start(link.name)
                raise AssertionError("symlink accepted")
            except telemetry_replay.ReplayError as exc:
                assert exc.code == "invalid_recording"
        for speed, skip in ((0, 0), (math.nan, 0), (1, -1), (1, math.inf)):
            try:
                control.start(path.name, speed=speed, skip=skip)
                raise AssertionError((speed, skip))
            except telemetry_replay.ReplayError as exc:
                assert exc.code == "invalid_options"


def t_clock():
    now = [10.0]
    waits = []
    def wait(delay):
        waits.append(delay)
        now[0] += delay
        return False
    stop = threading.Event()
    assert telemetry_replay.pace(12.0, stop, lambda: now[0], wait)
    assert abs(sum(waits) - 2) < 1e-9 and max(waits) <= 0.25
    stop.set()
    assert not telemetry_replay.pace(20, stop, lambda: now[0], wait)


def t_udp_does_not_wait_for_source_switch():
    store = gt7_telemetry.TelemetryStore()
    control = telemetry_replay.Controller(store, "unused")
    held, release = threading.Event(), threading.Event()
    def switching():
        with control._lock:
            held.set()
            release.wait(3)
    worker = threading.Thread(target=switching)
    worker.start()
    try:
        assert held.wait(1)
        started = time.monotonic()
        assert control.udp_update(None, 100) is False
        assert time.monotonic() - started < .1, "UDP reception blocked behind source reset"
    finally:
        release.set()
        worker.join()


def t_prefeed_and_switch():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "telemetry-recordings"
        root.mkdir()
        path = root / "private-name.gt7rec"
        Path(write_circle_recording(str(root))).rename(path)
        engineer = race_engineer_live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars())
        store = gt7_telemetry.TelemetryStore(engineer=engineer)
        control = telemetry_replay.Controller(store, root)
        try:
            generation = engineer.generation
            control.start(path.name, skip=0.5)
            limit = time.monotonic() + 5
            while control.status()["position_s"] < 30 and time.monotonic() < limit:
                time.sleep(.01)
            snapshot = engineer.snapshot()
            assert engineer.generation != generation
            assert snapshot["replay"]["active"]
            assert "private-name" not in str(snapshot["replay"])
            assert engineer.stats()["dropped"] == 0
            assert control.udp_update(None, 100) is False
            replay_generation = engineer.generation
            control.stop()
            assert not control.status()["active"]
            assert engineer.generation != replay_generation
            assert engineer.snapshot()["laps"] == []
            assert not engineer.snapshot()["replay"]["active"]
        finally:
            control.stop()
            engineer.close()


def t_http_guards_and_marker():
    from test_telemetry_endpoints import _serve
    from types import SimpleNamespace
    from urllib.parse import urlencode
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "telemetry-recordings"
        root.mkdir()
        path = Path(write_circle_recording(str(root)))
        store = gt7_telemetry.TelemetryStore(view_path=str(root.parent / "view.json"))
        store.replay = telemetry_replay.Controller(store, root)
        relay = SimpleNamespace(_obs=SimpleNamespace(get_health_stats=lambda: (True, {"stream_active": True}, "")), obs_stats={})
        server, get = _serve(store, relay)
        try:
            route = "/telemetry/replay/start?" + urlencode({"name": path.name})
            status, _, body = get(route)
            assert status == 409 and json.loads(body)["code"] == "on_air_confirmation"
            assert "--on-air" in json.loads(body)["error"]
            for name in ("../x.gt7rec", "x.csv", "missing.gt7rec"):
                status, _, body = get("/telemetry/replay/start?" + urlencode({"name": name, "on-air": "1"}))
                assert status == 400 and json.loads(body)["code"] == "invalid_recording"
            status, _, body = get(route + "&on-air=1")
            assert status == 200 and json.loads(body)["active"]
            status, _, body = get(route + "&on-air=1")
            assert status == 409 and json.loads(body)["code"] == "replay_active"
            assert get("/telemetry/replay/stop")[0] == 200
            status, _, body = get("/telemetry/replay-marker/hide")
            assert status == 200 and json.loads(body) == {"replay_marker": False}
            assert not gt7_telemetry.TelemetryStore(view_path=store._view_path).data()["replay_marker"]
            relay._obs.get_health_stats = lambda: (True, {"stream_active": False}, "")
            assert get(route)[0] == 200, "off-air start needs no confirmation"
        finally:
            store.replay.stop()
            server.shutdown()
            server.server_close()
        server, get = _serve(None)
        try:
            for verb in ("start", "stop", "status"):
                status, _, body = get("/telemetry/replay/" + verb)
                assert status == 404 and json.loads(body)["error"] == "telemetry replay disabled"
        finally:
            server.shutdown()
            server.server_close()


def t_import_and_argv():
    import test_racecast
    rc = test_racecast.m
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/ui"))
    import ui_ops
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        original = root / "original"
        original.mkdir()
        path = Path(write_circle_recording(str(original)))
        path.with_suffix(".laps.json").write_text('{"version": 8}', encoding="utf8")
        saved = rc._telemetry_rec_dir
        rc._telemetry_rec_dir = lambda: str(root / "destination")
        try:
            assert rc.telemetry_import_data(str(path))["ok"]
            target = root / "destination" / path.name
            assert target.read_bytes() == path.read_bytes()
            assert target.with_suffix(".laps.json").read_bytes() == path.with_suffix(".laps.json").read_bytes()
            try:
                rc.telemetry_import_data(str(path))
                raise AssertionError("collision accepted")
            except ValueError as exc:
                assert "already exists" in str(exc)
            invalid = original / "invalid.gt7rec"
            invalid.write_bytes(b"{}\n")
            try:
                rc.telemetry_import_data(str(invalid))
                raise AssertionError("invalid header imported")
            except Exception as exc:
                assert type(exc).__name__ == "RecordingError", exc
            assert not (root / "destination" / invalid.name).exists()
        finally:
            rc._telemetry_rec_dir = saved
        assert ui_ops.build_argv("telemetry-replay-start", {"name": path.name,"speed":2,"from":1,"on_air":True}) == ["telemetry", "replay", "start", path.name, "--speed", "2.0", "--from", "1.0", "--on-air"]
        assert ui_ops.build_argv("telemetry-import", {"file": str(path)}) == ["telemetry", "import", str(path)]


def t_offline_parity_and_udp_recording():
    import gt7_recording
    from test_gt7_laps import _index
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "telemetry-recordings"
        root.mkdir()
        path = Path(write_circle_recording(str(root), n=100))
        expected = _index(str(path))
        for speed in (1, 2, 100):
            engineer = race_engineer_live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars())
            captured = []
            class Store(gt7_telemetry.TelemetryStore):
                def reset_analysis(self, metadata, captured=captured):
                    if not metadata["active"]:
                        captured.append((self.engineer.snapshot(), dict(self.engineer._details)))
                    super().reset_analysis(metadata)
            store = Store(engineer=engineer)
            now = [0.0]
            def wait(delay, now=now):
                now[0] += delay
                return False
            control = telemetry_replay.Controller(store, root, clock=lambda now=now: now[0])
            real_pace = telemetry_replay.pace
            telemetry_replay.pace = lambda deadline, stop, clock, real_pace=real_pace, wait=wait: real_pace(deadline, stop, clock, wait)
            try:
                control.start(path.name, speed=speed)
                control._thread.join(10)
                assert not control._thread.is_alive()
                assert control.status()["error"] is None
                assert engineer.stats()["dropped"] == 0
                snapshot, details = captured[-1]
                assert snapshot["replay"]["active"]
                for lap in expected["laps"]:
                    if lap["gt7_time_s"] is None:
                        continue
                    actual = next(r for r in details.values() if r["lap"] == lap["lap"])
                    assert actual["gt7_time_s"] == lap["gt7_time_s"]
                    assert actual["time_s"] == lap["time_s"], (speed, lap["lap"], actual["time_s"], lap["time_s"])
                    assert actual["sectors"] == lap["sectors"]
                assert abs(now[0] - (expected["end_ts"] - expected["start_ts"]) / speed) < 1e-6
                packet = next(gt7_recording.Recording(str(path)).packets())
                assert control.udp_update(gt7_telemetry.parse_packet(packet[2]), packet[0])
                assert engineer.flush()
                assert engineer.snapshot()["current"]["lap"] == 1
            finally:
                telemetry_replay.pace = real_pace
                control.stop()
                engineer.close()


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("t_"):
            test()
            print("PASS", name)

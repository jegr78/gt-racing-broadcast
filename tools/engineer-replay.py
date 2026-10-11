#!/usr/bin/env python3
"""Serve the production Engineer desk from private copied recordings on loopback."""

import argparse
import importlib.util
import json
import math
import secrets
import shutil
import struct
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/scripts"))
import python_runtime

python_runtime.ensure_supported()
import gt7_cars
import gt7_context
import gt7_data
import gt7_recording
import gt7_laps
import gt7_telemetry
import gt7_tracks
import logsetup
import race_engineer_access
import race_engineer_api
import race_engineer_live

_RELAY = None
BATCH = 64


def relay_module():
    global _RELAY
    if _RELAY is None:
        spec = importlib.util.spec_from_file_location(
            "engineer_replay_relay", ROOT / "src/relay/racecast-feeds.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _RELAY = module
    return _RELAY


class EmptySchedule:
    """The handler receives a real solo Relay with no schedule or feed workers."""

    def get(self):
        return []

    def get_rows(self):
        return []


class ReplaySession:
    def __init__(
        self,
        recording,
        references=(),
        speed=1,
        skip_minutes=0,
        port=0,
        data_runtime=None,
        update_data=False,
        clock=time.monotonic,
        waiter=None,
    ):
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("speed must be finite and positive")
        if not math.isfinite(skip_minutes * 60) or skip_minutes < 0:
            raise ValueError("skip-minutes must be finite and nonnegative")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        self.input = Path(recording)
        self.references = list(references)
        self.speed, self.skip_s, self.port = speed, skip_minutes * 60, port
        self.data_runtime = Path(data_runtime) if data_runtime else ROOT / "runtime"
        self.update_data, self.clock = update_data, clock
        self.stop_event = threading.Event()
        self.waiter = waiter or self.stop_event.wait
        self._temporary = self.store = self.server = self._http_thread = None
        self._play_thread = None
        self.error = None
        self.packets = 0
        self.position_s = 0

    def _copy(self, source, name):
        if source.suffix != ".gt7rec":
            raise ValueError("only finalized .gt7rec recordings may be replayed")
        gt7_recording.Recording(str(source))
        target = self.profile / "telemetry-recordings" / name
        shutil.copyfile(source, target)
        sidecar = source.with_suffix(".context.json")
        if sidecar.is_file():
            shutil.copyfile(sidecar, target.with_suffix(".context.json"))
        return target

    def __enter__(self):
        try:
            self._temporary = tempfile.TemporaryDirectory(
                prefix="racecast-engineer-replay-"
            )
            self.runtime = Path(self._temporary.name)
            self.profile = self.runtime / "replay"
            (self.profile / "telemetry-recordings").mkdir(parents=True)
            self.recording = self._copy(self.input, "live.gt7rec")
            self.access = race_engineer_access.Store(self.profile, available=True)
            self.access.update(enabled=True, expected=0)
            for index, (source, label) in enumerate(self.references, 1):
                target = self._copy(Path(source), f"reference-{index}.gt7rec")
                self.access.update(
                    grant=gt7_context.source_identity(target),
                    label=label,
                    expected=self.access.read()["revision"],
                )
            cache = self.runtime / "gt7"
            cache.mkdir()
            for name in (
                *gt7_data.CAR_TABLES,
                "index.json",
                "signatures.json",
                gt7_data.LEARNED,
            ):
                source = self.data_runtime / "gt7" / name
                if source.is_file():
                    shutil.copyfile(source, cache / name)
            if self.update_data:
                result = gt7_data.update(str(self.runtime), force=True)
                if any(
                    value.startswith("error")
                    for value in result.get("files", {}).values()
                ):
                    print(
                        "Reference update failed; using valid cached/bundled data",
                        file=sys.stderr,
                    )
            self.tracks = gt7_tracks.TrackDB.load(str(self.runtime))
            self.cars = gt7_cars.CarDB(gt7_data.cars_dir(str(self.runtime)))
            self.store = race_engineer_live.Store(
                lambda: self.access.read()["enabled"],
                tracks=self.tracks,
                cars=self.cars,
                runtime_base=str(self.runtime),
            )
            self.store.bind_recording(str(self.recording))
            self.store.api = race_engineer_api.Service(self.store)
            module = relay_module()
            crew = module.CrewSource("")
            crew.inject_row(1, name="Engineer", race_engineer=True)
            secret = secrets.token_hex(32)
            self.token = module.console_auth.mint_token(secret, "engineer")
            relay = module.Relay(EmptySchedule(), [], str(self.runtime), solo=True)
            production = module.make_handler(
                relay,
                console_secret=secret,
                crew_source=crew,
                telemetry_store=SimpleNamespace(engineer=self.store),
                engineer_access=self.access,
                engineer_page_path=str(ROOT / "src/console/race-engineer.html"),
                chat_store=module.ChatStore(str(self.profile / "chat.json")),
            )

            class Handler(production):
                def do_GET(self):
                    path = urlsplit(self.path).path
                    if (
                        path == "/console/race-engineer"
                        or path.startswith("/console/race-engineer/")
                        or path == "/console/chat/data"
                    ):
                        return super().do_GET()
                    return self._send(
                        {
                            "error": {
                                "code": "not_found",
                                "message": "Replay desk route not found",
                            }
                        },
                        404,
                    )

                def do_POST(self):
                    if urlsplit(self.path).path == "/console/chat/send":
                        return super().do_POST()
                    return self._send(
                        {
                            "error": {
                                "code": "not_found",
                                "message": "Replay desk route not found",
                            }
                        },
                        404,
                    )

                def log_message(self, *_args):
                    pass  # Keep frequent browser polls out of the replay operator's console.

            self.server = module.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            self.origin = f"http://127.0.0.1:{self.server.server_port}"
            self.url = self.origin + "/console/race-engineer?t=" + self.token
            self._http_thread = threading.Thread(
                target=self.server.serve_forever, daemon=True
            )
            self._http_thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def flush(self, on_batch=None):
        if not self.store.flush(10):
            raise RuntimeError("replay preparation did not drain")
        if on_batch:
            on_batch(self)

    def play(self, on_batch=None):
        first = previous = None
        started = None
        batch = 0
        for wall, _kind, plain in gt7_recording.Recording(
            str(self.recording)
        ).packets():
            if self.stop_event.is_set():
                break
            if not math.isfinite(wall) or previous is not None and wall < previous:
                raise ValueError(
                    "recording timestamps must be finite and nondecreasing"
                )
            first = wall if first is None else first
            previous = wall
            self.position_s = wall - first
            if self.position_s >= self.skip_s:
                if started is None:
                    self.flush(on_batch)
                    started = self.clock()
                deadline = started + (self.position_s - self.skip_s) / self.speed
                if not self.wait_until(deadline):
                    break
            packet = gt7_telemetry.parse_packet(plain)
            if packet is None:
                raise ValueError("recording contains an invalid telemetry packet")
            if not self.store.put(packet, wall):
                self.flush(on_batch)
                if not self.store.put(packet, wall):
                    raise RuntimeError("replay input was not accepted")
            self.packets += 1
            batch += 1
            if batch >= BATCH:
                self.flush(on_batch)
                batch = 0
        self.flush(on_batch)
        if first is None:
            raise ValueError("recording has no telemetry packets")

    def wait_until(self, deadline):
        if not math.isfinite(deadline):
            raise ValueError("playback deadline is outside the supported range")
        while not self.stop_event.is_set():
            delay = deadline - self.clock()
            if delay <= 0:
                return True
            if self.waiter(min(delay, 0.25)):
                return False
        return False

    def start(self):
        def playback():
            try:
                self.play()
            except (
                ValueError,
                OSError,
                gt7_recording.RecordingError,
                RuntimeError,
            ) as exc:
                self.error = exc
            finally:
                self.finished.set()

        self.finished = threading.Event()
        self._play_thread = threading.Thread(
            target=playback, name="engineer-replay", daemon=True
        )
        self._play_thread.start()

    def close(self):
        self.stop_event.set()
        if self._play_thread is not None and self._play_thread.ident is not None:
            self._play_thread.join(timeout=15)
        if self.server is not None:
            if self._http_thread is not None and self._http_thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
        if self._http_thread is not None and self._http_thread.ident is not None:
            self._http_thread.join(timeout=5)
        if self.store is not None:
            self.store.close()
            # Private input copies must outlive every owned reader, including
            # one still finishing a large recording after the Store stop signal.
            for worker in (
                getattr(getattr(self.store, "api", None), "_worker", None),
                self.store._worker,
                self.store._reader,
            ):
                if worker is not None:
                    worker.join()
        if self._temporary is not None:
            self._temporary.cleanup()

    def __exit__(self, *_args):
        self.close()


def synthetic_recording(directory):
    """Synthetic complete circle plus a growing current lap, using real packets."""
    radius = 1000 / (2 * math.pi)
    writer = gt7_recording.RecordingWriter(
        str(directory), "Replay check", "dev", queue_max=0
    )
    wall, last = 1_700_000_000.0, -1
    try:
        for li, seconds in enumerate((20, 20, 16, 20, 20)):
            for sample in range(200 if li < 4 else 120):
                if li >= 2 and sample == 5:
                    last = (20, 20, 16, 20)[li - 1] * 1000
                angle = 2 * math.pi * sample / 200
                packet = bytearray(344)
                struct.pack_into("<I", packet, 0, 0x47375330)
                struct.pack_into(
                    "<3f",
                    packet,
                    gt7_telemetry.OFF_POS,
                    radius * math.cos(angle),
                    0,
                    radius * math.sin(angle),
                )
                for offset, value in (
                    (gt7_telemetry.OFF_SPEED, 1000 / seconds),
                    (gt7_telemetry.OFF_RPM, 7000),
                    (gt7_telemetry.OFF_FUEL_LEVEL, 50),
                    (gt7_telemetry.OFF_FUEL_CAP, 100),
                    (gt7_telemetry.OFF_STEER, 0.1),
                ):
                    struct.pack_into("<f", packet, offset, value)
                for offset in (
                    gt7_telemetry.OFF_TYRE_FL,
                    gt7_telemetry.OFF_TYRE_FR,
                    gt7_telemetry.OFF_TYRE_RL,
                    gt7_telemetry.OFF_TYRE_RR,
                ):
                    struct.pack_into("<f", packet, offset, 80)
                struct.pack_into("<h", packet, gt7_telemetry.OFF_LAP, li + 1)
                struct.pack_into("<i", packet, gt7_telemetry.OFF_LAST_MS, last)
                struct.pack_into("<i", packet, gt7_telemetry.OFF_BEST_MS, -1)
                struct.pack_into("<i", packet, gt7_telemetry.OFF_CAR_ID, 3424)
                struct.pack_into(
                    "<H", packet, gt7_telemetry.OFF_FLAGS, gt7_telemetry.FLAG_ON_TRACK
                )
                packet[gt7_telemetry.OFF_THROTTLE] = 255
                packet[gt7_telemetry.OFF_GEAR] = 4
                writer.put(wall, "~", packet)
                wall += seconds / 200
    finally:
        writer.close()
    if writer.error or writer.dropped:
        raise RuntimeError("synthetic recording creation failed")
    return Path(writer.path)


def http(session, route, **query):
    url = (
        session.origin
        + "/console/race-engineer/"
        + route
        + "?"
        + urlencode(dict(query, t=session.token))
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "Racecast/engineer-replay"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def completed(session, a, b, distance=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = http(session, "compare", a=a, b=b)
        if result["state"] == "ready" and (
            distance is None or result["b"]["distance_m"] == distance
        ):
            return result
        time.sleep(0.01)
    raise AssertionError("replay comparison did not complete")


def check():
    with tempfile.TemporaryDirectory(prefix="racecast-replay-check-") as directory:
        root = Path(directory)
        source = synthetic_recording(root)
        with ReplaySession(source, speed=10000, data_runtime=root / "empty") as session:
            radius = 1000 / (2 * math.pi)
            session.tracks.learn(
                "replay-ci",
                [
                    (
                        radius * math.cos(i * math.pi / 100),
                        radius * math.sin(i * math.pi / 100),
                    )
                    for i in range(200)
                ],
                1000,
            )
            previous = {}
            revisions = set()
            growing = 0

            def observe(replay):
                nonlocal growing
                if not replay.packets:
                    return
                snapshot = http(replay, "snapshot")
                revisions.add(snapshot["revision"])
                history = http(replay, "history")
                eligible = [
                    row for row in history.get("laps", []) if row["comparison_eligible"]
                ]
                current = snapshot.get("current")
                if (
                    not eligible
                    or not current
                    or not current["provisional_comparison_eligible"]
                ):
                    return
                a, b = eligible[0]["id"], current["id"]
                pair = a, b
                result = http(replay, "compare", a=a, b=b)
                if pair in previous and current["distance_m"] > previous[pair]:
                    assert result["state"] == "ready", (
                        "growing replay lost its completed comparison"
                    )
                    assert result["delta"] and result["mini_sectors"]["a"], (
                        "live delta/A mini-sectors unavailable"
                    )
                    assert (
                        result["provisional"]
                        and result["b"]["distance_m"] <= current["distance_m"]
                    )
                    growing += 1
                result = completed(replay, a, b, current["distance_m"])
                assert result["compatible"] and result["provisional"]
                previous[pair] = current["distance_m"]

            session.play(observe)
            history = http(session, "history")
            confirmed = [row for row in history["laps"] if row["state"] == "confirmed"]
            assert len(revisions) > 5 and len(confirmed) >= 2 and growing >= 3, (
                "replay did not exercise moving confirmed/provisional analysis"
            )
            assert session.store.stats()["dropped"] == 0, (
                "replay overflowed the bounded input queue"
            )
            offline = gt7_laps.index(
                str(session.recording),
                session.tracks,
                session.cars,
                str(session.runtime),
                key="replay/live",
            )
            for row in confirmed:
                expected = next(
                    lap for lap in offline["laps"] if lap["lap"] == row["lap"]
                )
                detail = http(session, "lap", id=row["id"])["lap"]
                assert detail["gt7_time_s"] == expected["gt7_time_s"]
                assert detail["sectors"] == expected["sectors"], (
                    "accelerated replay changed recorded timing"
                )
    print(
        "PASS recording replay: advancing snapshots, confirmed laps and growing provisional delta/sectors"
    )
    return 0


def main():
    logsetup.harden_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", nargs="?", help="Finalized .gt7rec input")
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        help="Reference recording, repeatable",
    )
    parser.add_argument(
        "--reference-label",
        action="append",
        default=[],
        help="Shared label, one per reference",
    )
    parser.add_argument(
        "--speed", type=float, default=1, help="Positive playback speed, default 1"
    )
    parser.add_argument(
        "--skip-minutes",
        type=float,
        default=0,
        help="Instant bounded pre-feed in minutes",
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Loopback HTTP port, default a free port"
    )
    parser.add_argument(
        "--data-runtime", type=Path, help="Read existing GT7 cache from this runtime"
    )
    parser.add_argument(
        "--update-data",
        action="store_true",
        help="Explicitly download reference data into the temporary runtime",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Synthetic growing-lap HTTP regression check",
    )
    args = parser.parse_args()
    if args.check:
        return check()
    if not args.recording:
        parser.error("recording is required unless --check is used")
    if args.reference_label and len(args.reference_label) != len(args.reference):
        parser.error("provide one --reference-label per --reference")
    labels = args.reference_label or [
        f"Reference {i}" for i in range(1, len(args.reference) + 1)
    ]
    try:
        with ReplaySession(
            args.recording,
            list(zip(args.reference, labels, strict=True)),
            args.speed,
            args.skip_minutes,
            args.port,
            args.data_runtime,
            args.update_data,
        ) as session:
            print("Replay desk: " + session.url, flush=True)
            print(
                "Private temporary copies; press Ctrl+C to close the desk", flush=True
            )
            session.start()
            while not session.finished.wait(0.2):
                continue
            if session.error:
                raise session.error
            print(
                "Replay complete; the desk remains available until Ctrl+C", flush=True
            )
            while not session.stop_event.wait(0.5):
                continue
    except KeyboardInterrupt:
        pass  # The context manager stops owned workers and removes private copies.
    except (ValueError, OSError, gt7_recording.RecordingError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

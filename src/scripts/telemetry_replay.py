"""Profile-owned telemetry replay through the running relay's normal consumers."""
import math
import threading
import time
from pathlib import Path

import gt7_recording
import gt7_telemetry
import race_engineer_access

BATCH = 64


class ReplayError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def pace(deadline, stop, clock=time.monotonic, wait=None):
    if not math.isfinite(deadline):
        raise ValueError("Replay deadline is outside the supported clock range")
    wait = wait or stop.wait
    while not stop.is_set():
        remaining = deadline - clock()
        if remaining <= 0:
            return True
        if wait(min(.25, remaining)):
            return False
    return False


class Controller:
    def __init__(self, store, directory, clock=time.monotonic):
        self.store = store
        self.directory = Path(directory)
        self.clock = clock
        self._lock = threading.RLock()
        self._operations = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._state = dict(active=False, label="", speed=1.0, position_s=0.0,
                           duration_s=0.0, error=None)

    def status(self):
        with self._lock:
            return dict(self._state)

    def _reset(self, active, label="", speed=1.0):
        metadata = dict(active=active, label=label, speed=speed, live_gap=not active)
        self.store.reset_analysis(metadata)

    def udp_update(self, packet, wall):
        # Switching may wait for analysis; the UDP thread must keep recording.
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self._state["active"]:
                return False
            self.store.update(packet, wall)
            return True
        finally:
            self._lock.release()

    def _path(self, name):
        if not isinstance(name, str) or not name or name.startswith("-") or any(ord(c) < 32 for c in name):
            raise ReplayError("invalid_recording", "Select a finalized profile recording")
        access = race_engineer_access.Store(self.directory.parent, available=True)
        try:
            path = access._path(name)
            if not path.is_file():
                raise ValueError("not a regular recording")
            gt7_recording.Recording(str(path))
            return path
        except (race_engineer_access.AccessError, gt7_recording.RecordingError, OSError, ValueError) as exc:
            raise ReplayError("invalid_recording", "Select a readable finalized profile recording") from exc

    def start(self, name, speed=1.0, skip=0.0):
        try:
            speed, skip = float(speed), float(skip)
            if not math.isfinite(speed) or not 0 < speed <= 100 or not math.isfinite(skip) or not 0 <= skip <= 10080:
                raise ValueError("range")
        except (ValueError, TypeError, OverflowError) as exc:
            raise ReplayError("invalid_options", "Speed must be > 0 and <= 100; from must be 0..10080 minutes") from exc
        path = self._path(name)
        with self._operations:
            if self.status()["active"]:
                raise ReplayError("replay_active", "Stop the current replay first")
            if self._thread is not None:
                self._thread.join()
            self._stop = threading.Event()
            # Deliberately independent of the private filename and header profile.
            label = "Recorded session"
            with self._lock:
                self._reset(True, label, speed)
                self._state = dict(active=True, label=label, speed=speed,
                                   position_s=0.0, duration_s=0.0, error=None)
            self._thread = threading.Thread(target=self._run, args=(path, speed, skip * 60),
                                            name="telemetry-replay", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self):
        with self._operations:
            self._stop.set()
            if self._thread is not None:
                self._thread.join()
            return self.status()

    def _run(self, path, speed, skip):
        error = None
        try:
            recording = gt7_recording.Recording(str(path))
            first = previous = last = None
            for wall, _kind, _plain in recording.packets():
                if self._stop.is_set():
                    return
                if not math.isfinite(wall) or previous is not None and wall < previous:
                    raise ValueError("Recording timestamps must be finite and ordered")
                first = wall if first is None else first
                previous = last = wall
            if first is None:
                raise ValueError("Recording contains no telemetry packets")
            if not math.isfinite(last - first):
                raise ValueError("Recording duration is outside the supported clock range")
            with self._lock:
                self._state["duration_s"] = last - first
            origin = self.clock()
            count = 0
            paced = False
            for wall, _kind, plain in recording.packets():
                position = wall - first
                if position >= skip and not paced:
                    if self.store.engineer is not None and not self.store.engineer.flush():
                        raise ValueError("Replay prefeed did not drain")
                    origin = self.clock()
                    paced = True
                if position >= skip and not pace(origin + (position - skip) / speed,
                                                 self._stop, self.clock):
                    break
                if self._stop.is_set():
                    break
                with self._lock:
                    self.store.update(gt7_telemetry.parse_packet(plain), wall)
                    self._state["position_s"] = position
                count += 1
                if count % BATCH == 0:
                    engineer = self.store.engineer
                    if engineer is not None and not engineer.flush():
                        raise ValueError("Replay analysis did not drain")
                    # Prefeeding must not eat into the paced interval.
                    if position < skip:
                        origin = self.clock()
            if self.store.engineer is not None and not self.store.engineer.flush():
                raise ValueError("Replay analysis did not drain")
        except (OSError, ValueError, gt7_recording.RecordingError) as exc:
            error = str(exc) if isinstance(exc, ValueError) else "Recording could not be replayed"
        finally:
            with self._lock:
                self._reset(False)
                self._state["active"] = False
                self._state["error"] = error

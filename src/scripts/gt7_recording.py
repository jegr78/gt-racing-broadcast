#!/usr/bin/env python3
"""GT7 telemetry recording: the relay's decrypted packets on disk, read back, exported.

Stdlib only and no relay imports, so `racecast telemetry export` runs without a relay.
File: one JSON header line, then records of float64 wall ts, uint8 kind, uint16
length and the payload. Kind 0x00 is a meta record carrying {"dropped": n}.
"""
import csv
import datetime
import json
import logging
import math
import os
import queue
import struct
import threading
import time

import gt7_cars
import gt7_telemetry

LOG = logging.getLogger("racecast.relay.telemetry")

FORMAT = "racecast-gt7rec"
VERSION = 1
SUFFIX = ".gt7rec"
PART = ".part"
QUEUE_MAX = 600               # about 10 s of packets at 60 Hz
FLUSH_S = 1.0
RENAME_TRIES = 3
RENAME_WAIT_S = 0.2
_REC = struct.Struct("<dBH")
_META = 0x00
_DONE = object()              # close() sentinel: wakes the writer without waiting FLUSH_S


class RecordingError(Exception):
    """A file that is not a readable racecast telemetry recording."""


def _encode(wall_ts, kind_byte, payload):
    return _REC.pack(wall_ts, kind_byte, len(payload)) + payload


def _local_dt(ts):
    """ts as a local-timezone datetime, via an explicit UTC tz so the conversion never
    calls the platform's raw localtime(): on Windows that raises OSError (Errno 22)
    for small ts, which near-epoch test fixtures hit."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone()


def _header(profile, relay_version, started_ts):
    started = _local_dt(started_ts).isoformat(timespec="seconds")
    return (json.dumps({"format": FORMAT, "version": VERSION, "profile": profile,
                        "started": started, "relay_version": relay_version})
            + "\n").encode("utf-8")


def _sanitize_error(e):
    """A /status-safe message: never the OS path from an OSError, which reaches every
    console role (#786). strerror has no path; fall back to the exception class name."""
    return (e.strerror or type(e).__name__) if isinstance(e, OSError) else type(e).__name__


def _free_path(rec_dir, stem):
    """`<stem>.gt7rec` in rec_dir, or `<stem>-N.gt7rec` when that name (or its .part) exists."""
    n = 1
    while True:
        name = stem + ("" if n == 1 else f"-{n}") + SUFFIX
        path = os.path.join(rec_dir, name)
        if not os.path.exists(path) and not os.path.exists(path + PART):
            return path
        n += 1


def recording_stem(path):
    name = os.path.basename(path)
    if name.endswith(PART):
        name = name[:-len(PART)]
    return name[:-len(SUFFIX)] if name.endswith(SUFFIX) else name


class RecordingWriter:
    """Writes packets to `<rec_dir>/<start>.gt7rec.part` from its own thread and
    renames the file on close. put() never blocks: a full queue drops the packet."""

    def __init__(self, rec_dir, profile, relay_version, queue_max=QUEUE_MAX,
                 flush_s=FLUSH_S):
        self._dir = rec_dir
        self._profile = profile
        self._relay_version = relay_version
        self._flush_s = flush_s
        self._q = queue.Queue(maxsize=queue_max)
        self._stop = threading.Event()
        self.path = None
        self.started = None
        self.bytes = 0
        self.dropped = 0
        self.error = None
        self._thread = threading.Thread(target=self._run, name="gt7-recorder", daemon=True)
        self._thread.start()

    def put(self, wall_ts, kind, plain):
        if self.error is not None or self._stop.is_set():
            return
        try:
            self._q.put_nowait((wall_ts, kind, plain))
        except queue.Full:
            self.dropped += 1

    def close(self, timeout=5.0):
        self._stop.set()
        if self._thread.is_alive():
            try:
                self._q.put(_DONE, timeout=min(timeout, self._flush_s))
            except queue.Full:
                pass  # the writer still ends after its next empty get() timeout
        self._thread.join(timeout)
        if not self._thread.is_alive():
            self._drain()             # a writer that died early leaves the sentinel behind

    def _open(self, first_ts):
        os.makedirs(self._dir, exist_ok=True)
        stem = _local_dt(first_ts).strftime("%Y%m%d-%H%M%S")
        path = _free_path(self._dir, stem)
        fh = open(path + PART, "wb")  # noqa: SIM115  kept open across the writer loop
        head = _header(self._profile, self._relay_version, first_ts)
        fh.write(head)
        self.started = first_ts
        self.bytes = len(head)
        self.path = path              # last: a visible path always has a start time
        return fh

    def _write(self, fh, data):
        fh.write(data)
        self.bytes += len(data)

    def _drain(self):
        """Every packet still queued behind the close() sentinel."""
        items = []
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return items
            if item is not _DONE:
                items.append(item)

    def _rename(self):
        """Drop the .part suffix, retrying while another process (Windows AV,
        indexer) still holds the file; a lasting failure waits for finalize_partials."""
        for attempt in range(RENAME_TRIES):
            try:
                os.replace(self.path + PART, self.path)
                return
            except OSError as e:
                err = e
            if attempt + 1 < RENAME_TRIES:
                time.sleep(RENAME_WAIT_S)
        self.error = _sanitize_error(err)
        LOG.warning("could not finalise telemetry recording %s: %s; the next relay "
                    "start finishes it", os.path.basename(self.path) + PART, err.strerror)

    def _run(self):
        fh = None
        written_drops = 0
        last_flush = time.monotonic()
        try:
            while True:
                try:
                    item = self._q.get(timeout=self._flush_s)
                except queue.Empty:
                    item = None
                done = item is _DONE or (item is None and self._stop.is_set())
                if item is _DONE:
                    batch = self._drain()
                else:
                    batch = [] if item is None else [item]
                for wall_ts, kind, plain in batch:
                    if fh is None:
                        fh = self._open(wall_ts)
                    self._write(fh, _encode(wall_ts, ord(kind), plain))
                now = time.monotonic()
                if fh is not None and (done or now - last_flush >= self._flush_s):
                    if self.dropped != written_drops:
                        written_drops = self.dropped
                        meta = json.dumps({"dropped": written_drops}).encode("utf-8")
                        self._write(fh, _encode(time.time(), _META, meta))
                    fh.flush()
                    last_flush = now
                if done:
                    break
        except Exception as e:  # noqa: BLE001  any writer failure must stop recording, not die silently
            self.error = _sanitize_error(e)
            LOG.warning("telemetry recording stopped: %s", e)
        finally:
            if fh is not None:
                try:
                    fh.close()
                except OSError as e:
                    self.error = self.error or _sanitize_error(e)
                if self.error is None:
                    self._rename()


class Recording:
    """Read access to one recording file (finished or .part)."""

    def __init__(self, path):
        self.path = path
        self.dropped = 0
        try:
            with open(path, "rb") as fh:
                line = fh.readline()
                self._offset = fh.tell()
        except OSError as e:
            raise RecordingError(f"{path}: {e}") from e
        try:
            header = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            header = None
        if not isinstance(header, dict) or header.get("format") != FORMAT:
            raise RecordingError(f"{path}: not a racecast telemetry recording")
        version = header.get("version")
        if not isinstance(version, int) or version > VERSION:
            raise RecordingError(
                f"{path}: format version {version} is newer than this racecast "
                f"reads ({VERSION}); update racecast")
        self.header = header

    def packets(self):
        """Yield (wall_ts, kind, plain) per packet; a truncated last record ends it."""
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            while True:
                head = fh.read(_REC.size)
                if len(head) < _REC.size:
                    return
                wall_ts, kind, n = _REC.unpack(head)
                payload = fh.read(n)
                if len(payload) < n:
                    return
                if kind == _META:
                    try:
                        self.dropped = int(json.loads(payload.decode("utf-8"))["dropped"])
                    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                        pass  # a damaged meta record only loses the drop count
                    continue
                yield wall_ts, chr(kind), payload


def finalize_partials(rec_dir):
    """Rename every leftover `.gt7rec.part` in rec_dir to `.gt7rec`; returns the new names."""
    try:
        names = sorted(os.listdir(rec_dir))
    except OSError:
        return []
    done = []
    for name in names:
        if not name.endswith(SUFFIX + PART):
            continue
        target = os.path.join(rec_dir, name[:-len(PART)])
        if os.path.exists(target):    # _free_path alone would count this .part as taken
            target = _free_path(rec_dir, name[:-len(SUFFIX + PART)])
        try:
            os.replace(os.path.join(rec_dir, name), target)
            done.append(os.path.basename(target))
        except OSError as e:
            LOG.warning("could not finalise telemetry recording %s: %s", name, e)
    return done


def _started_ts(header):
    try:
        return datetime.datetime.fromisoformat(header.get("started", "")).timestamp()
    except (TypeError, ValueError):
        return None


def list_recordings(rec_dir, count_laps=False):
    """One dict per readable recording in rec_dir, sorted by name. count_laps replays
    every packet through the engine and reports the laps export_csv would write."""
    try:
        names = sorted(os.listdir(rec_dir))
    except OSError:
        return []
    rows = []
    for name in names:
        if not (name.endswith(SUFFIX) or name.endswith(SUFFIX + PART)):
            continue
        path = os.path.join(rec_dir, name)
        try:
            r = Recording(path)
        except RecordingError:
            continue
        if not count_laps:
            start = _started_ts(r.header)
            rows.append({"name": name, "path": path, "size": os.path.getsize(path),
                         "started": r.header.get("started", ""),
                         "duration_s": max(0.0, os.path.getmtime(path) - start) if start else 0.0,
                         "laps": None, "partial": name.endswith(PART)})
            continue
        first = last = None
        laps = []
        eng = gt7_telemetry.TelemetryEngine()
        eng.on_lap = laps.append      # the same lap records export_csv writes
        for wall_ts, _kind, plain in r.packets():
            first = wall_ts if first is None else first
            last = wall_ts
            eng.update(gt7_telemetry.parse_packet(plain), wall_ts)
        rows.append({"name": name, "path": path, "size": os.path.getsize(path),
                     "started": r.header.get("started", ""),
                     "duration_s": (last - first) if first is not None else 0.0,
                     "laps": len(laps), "partial": name.endswith(PART)})
    return rows


SAMPLE_COLUMNS = (
    "t_s", "session", "lap", "lap_t_s", "lap_dist_m", "on_track", "paused", "speed_kmh",
    "throttle_pct", "brake_pct", "throttle_input_pct", "brake_input_pct", "steer_deg",
    "gear", "rpm", "fuel_l", "tyre_fl_c", "tyre_fr_c", "tyre_rl_c", "tyre_rr_c",
    "pos_x", "pos_y", "pos_z", "car_id")
LAP_COLUMNS = (
    "session", "lap", "start_t_s", "end_t_s", "gt7_time_s", "relay_time_s", "status",
    "reason", "fuel_used_l", "top_speed_kmh", "car")
GT7_TIME_WINDOW_S = 3.0       # GT7 updates last_ms shortly after the line


def _fmt(excel):
    def num(value, digits):
        if value is None or not math.isfinite(value):
            return ""
        text = f"{value:.{digits}f}"
        return text.replace(".", ",") if excel else text
    return num


def _pct(byte):
    return None if byte is None else byte * 100.0 / 255.0


def _car_name(cars, car_id):
    car = cars.lookup(car_id) if car_id is not None else None
    if car is None:
        return ""
    return f"{car['maker']} {car['name']}" if car.get("maker") else car["name"]


def export_csv(path, out_dir, include_all=False, excel=False, cars=None):
    """Write samples.csv and laps.csv for one recording into out_dir."""
    cars = cars if cars is not None else gt7_cars.CarDB()
    r = Recording(path)
    num = _fmt(excel)
    eng = gt7_telemetry.TelemetryEngine()
    laps, waiting = [], []
    eng.on_lap = laps.append
    os.makedirs(out_dir, exist_ok=True)
    delimiter = ";" if excel else ","
    encoding = "utf-8-sig" if excel else "utf-8"
    t0 = None
    prev_last_ms = None
    written = 0
    with open(os.path.join(out_dir, "samples.csv"), "w", newline="",
              encoding=encoding) as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(SAMPLE_COLUMNS)
        for wall_ts, _kind, plain in r.packets():
            t0 = wall_ts if t0 is None else t0
            pkt = gt7_telemetry.parse_packet(plain)
            closed = len(laps)
            eng.update(pkt, wall_ts)
            for lap in laps[closed:]:
                lap["gt7_time_s"] = None
                waiting.append((lap, wall_ts + GT7_TIME_WINDOW_S, prev_last_ms))
            for item in list(waiting):
                lap, deadline, before = item
                if pkt.last_ms > 0 and pkt.last_ms != before:
                    lap["gt7_time_s"] = pkt.last_ms / 1000.0
                    waiting.remove(item)
                elif wall_ts > deadline:
                    waiting.remove(item)
            prev_last_ms = pkt.last_ms
            if not include_all and (not pkt.on_track or pkt.paused or pkt.loading):
                continue
            started = eng.lap_started_at()
            steer = None if pkt.steer_rad is None else math.degrees(pkt.steer_rad)
            w.writerow([
                num(wall_ts - t0, 3), eng.session, pkt.lap,
                num(wall_ts - started if started is not None else None, 3),
                num(eng.lap_distance(), 1), int(pkt.on_track), int(pkt.paused), num(pkt.speed_mps * 3.6, 1),
                num(_pct(pkt.throttle), 1), num(_pct(pkt.brake), 1),
                num(_pct(pkt.throttle_input), 1), num(_pct(pkt.brake_input), 1),
                num(steer, 1), pkt.gear, num(pkt.rpm, 0), num(pkt.fuel_level, 2),
                *(num(v, 1) for v in pkt.tyre_temp),
                num(pkt.pos_x, 2), num(pkt.pos_y, 2), num(pkt.pos_z, 2),
                "" if pkt.car_id is None else pkt.car_id])
            written += 1
    with open(os.path.join(out_dir, "laps.csv"), "w", newline="", encoding=encoding) as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(LAP_COLUMNS)
        for lap in laps:
            w.writerow([
                lap["session"], lap["lap"], num(lap["start"] - t0, 3),
                num(lap["end"] - t0, 3), num(lap["gt7_time_s"], 3),
                num(lap["elapsed"], 3), lap["status"], lap["reason"],
                num(lap["fuel_used"], 2), num(lap["top_speed_mps"] * 3.6, 1),
                _car_name(cars, lap["car_id"])])
    return {"dir": out_dir, "samples": written, "laps": len(laps), "dropped": r.dropped}


_TRUTHY = {"1", "true", "yes", "on"}


def record_default(environ):
    """The profile's TELEMETRY_RECORD default as injected by the CLI."""
    return str(environ.get("RACECAST_TELEMETRY_RECORD", "")).strip().lower() in _TRUTHY


class RecordControl:
    """The live recording switch plus the open writer. A valid `state_path`
    ({"active": bool}) wins over `default`; every set/toggle rewrites it."""

    def __init__(self, rec_dir, state_path, default, profile="", relay_version="dev",
                 writer_factory=None):
        self._lock = threading.Lock()
        self._state_path = state_path
        self._active = self._load(default)
        self._writer = None
        self._error = None
        self._closed = False
        self._factory = writer_factory or (
            lambda: RecordingWriter(rec_dir, profile, relay_version))

    def _load(self, default):
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                v = json.load(fh).get("active")
            return v if isinstance(v, bool) else bool(default)
        except (OSError, ValueError, AttributeError):
            return bool(default)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"active": self._active}, fh)
            os.replace(tmp, self._state_path)
        except OSError:
            pass  # best-effort, never crash the relay

    def put(self, wall_ts, kind, plain):
        bad = None
        with self._lock:
            if self._closed or not self._active or self._error is not None:
                return
            if self._writer is None:
                try:
                    self._writer = self._factory()
                except Exception as e:  # noqa: BLE001  a bad writer must never reach the UDP loop
                    self._error = _sanitize_error(e)
                    return
            w = self._writer
            if w.error is not None:
                self._error = w.error
                bad = self._detach_writer()
        if bad is not None:
            bad.close()   # outside the lock: close() can block on its writer thread
            return
        w.put(wall_ts, kind, plain)

    def _detach_writer(self):
        """Pop the open writer so a slow close() runs outside `self._lock`."""
        w, self._writer = self._writer, None
        return w

    def set_active(self, on):
        with self._lock:
            self._active = bool(on)
            self._error = None
            w = self._detach_writer() if not self._active else None
            self._save()
            brief = self._brief()
        if w is not None:
            w.close()
        return brief

    def toggle(self):
        with self._lock:
            self._active = not self._active
            self._error = None
            w = self._detach_writer() if not self._active else None
            self._save()
            brief = self._brief()
        if w is not None:
            w.close()
        return brief

    def _brief(self):
        w = self._writer
        return {"active": self._active,
                "file": os.path.basename(w.path) if w is not None and w.path else None,
                "since": w.started if w is not None else None}

    def status(self, now=None):
        """now overrides the relay wall clock for elapsed_s (tests only)."""
        with self._lock:
            out = self._brief()
            w = self._writer
            out["bytes"] = w.bytes if w is not None else 0
            out["dropped"] = w.dropped if w is not None else 0
            out["error"] = self._error or (w.error if w is not None else None)
            since = out["since"]
            out["elapsed_s"] = None if since is None else max(
                0.0, (time.time() if now is None else now) - since)
            return out

    def close(self):
        """Terminal: stop accepting packets for good (the relay is exiting). Unlike
        set_active(False), this never flips `active` or rewrites the state file, so
        the next relay start resumes recording if it was on."""
        with self._lock:
            self._closed = True
            w = self._detach_writer()
        if w is not None:
            w.close()

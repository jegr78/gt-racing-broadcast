"""One bounded live analysis worker and one shared local recording recovery worker."""

import collections
import copy
import hashlib
import json
import math
import os
import queue
import threading
import time
import uuid

import gt7_context
import gt7_track_definitions
import gt7_laps
import gt7_recording
import gt7_shifts
import gt7_telemetry

PACKETS_MAX = 2048
SAMPLES_MAX = 20001
PENDING_MAX = 4
SESSIONS_MAX = 8
SUMMARIES_MAX = 512
DETAILS_MAX = 16
DETAIL_BYTES_MAX = 16 * 1024 * 1024
ENTRY_BYTES_MAX = 1024 * 1024
RECOVERY_MAX = 8
STALE_S = 5.0
# Throttle/brake history for the desk's input trace, decimated like the HUD's.
INPUT_TRACE_S = 6.0
INPUT_TRACE_DT = gt7_telemetry.TRACE_MIN_DT
SUMMARY_FIELDS = frozenset((
    "measurement_hash",
    "_context_source",
    "id",
    "session_id",
    "version",
    "context_revision",
    "_start_wall",
    "_end_wall",
    "recording_session",
    "session",
    "lap",
    "state",
    "detail_state",
    "gt7_time_s",
    "time_s",
    "relay_time_s",
    "status",
    "reason",
    "car_id",
    "car",
    "track_id",
    "track",
    "layout",
    "distance_m",
    "length_m",
    "fuel_used_l",
    "top_speed_kmh",
    "line_position",
    "line_car_count",
    "lap_role",
    "after_service",
    "capture_complete",
    "trace_complete",
    "time_valid",
    "data_quality",
    "reasons",
    "pace_eligible",
    "comparison_eligible",
    "analysis_role",
    "compound",
    "elapsed_s",
    "observed_from_line",
    "provisional_comparison_eligible",
    "definition_fingerprint",
    "sector_variant_id",
    "track_definition_warnings",
    "context_confirmed",
    "context_warnings",
    "tyre_age_laps",
    "start_t_s",
    "end_t_s",
))


def _summary(row):
    result = {k: v for k, v in row.items() if k in SUMMARY_FIELDS}
    # Eligible laps have no contradictory known strategy values. Preserve just
    # those constraints for shared compatible(), without repeating free notes.
    strategy = {}
    for declared in row.get("strategies") or [row.get("strategy", {})]:
        strategy.update({
            k: declared[k]
            for k in ("fuel_map", "shortshift")
            if declared.get(k) is not None
        })
        if declared.get("targets"):
            strategy.setdefault("targets", {}).update(declared["targets"])
    result.update(
        context_settings={
            k: v
            for k, v in row.get("context_settings", {}).items()
            if k
            in (
                "bop",
                "fixed_setup",
                "tyre_x",
                "fuel_x",
                "time_progression",
                "time_of_day",
            )
        },
        settings_confirmed=row.get("settings_confirmed", False),
        strategy=strategy,
        strategies=[strategy],
    )
    return result


def _measurement_hash(row):
    keys = (
        "gt7_time_s",
        "trace",
        "sectors",
        "points",
        "fuel_used_l",
        "car_id",
        "capture_complete",
        "data_quality",
        "distance_m",
    )
    raw = json.dumps(
        {k: row.get(k) for k in keys}, sort_keys=True, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def fuel_average(laps, session_id):
    """(mean litres per lap, lap count) over the live session's completed eligible laps.
    A missing or zero use is no measurement (no fuel use / refuel), never a zero average."""
    used = [r["fuel_used_l"] for r in laps
            if session_id is not None and r.get("session_id") == session_id
            and r.get("state") == "confirmed" and r.get("comparison_eligible")
            and _finite(r.get("fuel_used_l")) is not None and r["fuel_used_l"] > 0]
    return (round(sum(used) / len(used), 3), len(used)) if used else (None, 0)


def instruments(pkt):
    """Only measured channels; a missing/nonfinite channel stays unavailable."""
    x, z = _finite(pkt.pos_x), _finite(pkt.pos_z)
    return {
        "position": {"x": x, "z": z}
        if pkt.on_track and not pkt.loading and x is not None and z is not None
        else None,
        "race_position": pkt.race_position if pkt.on_track and not pkt.loading else None,
        "car_count": pkt.car_count if pkt.on_track and not pkt.loading else None,
        "speed_kmh": _finite(pkt.speed_mps * 3.6),
        "gear": _finite(pkt.gear),
        "rpm": _finite(pkt.rpm),
        "throttle": _finite(pkt.throttle / 255 * 100),
        "brake": _finite(pkt.brake / 255 * 100),
        "fuel_l": _finite(pkt.fuel_level),
        "fuel_capacity_l": _finite(pkt.fuel_capacity),
        "tyres_c": [_finite(v) for v in pkt.tyre_temp],
        "steer_rad": _finite(pkt.steer_rad),
    }


def _record_stamp(source, path):
    st = os.stat(path)
    context_path = gt7_context._stem(path) + ".context.json"
    try:
        ctx = os.stat(context_path)
        context_stamp = (
            ctx.st_dev,
            ctx.st_ino,
            ctx.st_size,
            ctx.st_mtime_ns,
            ctx.st_ctime_ns,
        )
    except FileNotFoundError:
        context_stamp = (None, None, None, None, None)
    return (
        source,
        os.path.realpath(path),
        st.st_dev,
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
        context_stamp,
    )


def _prefix_digest(path, size, stopped=None):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        remaining = size
        while remaining:
            if stopped and stopped():
                raise ValueError("Recovery stopped")
            chunk = fh.read(min(65536, remaining))
            if not chunk:
                raise ValueError("Recording prefix truncated")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _bounded_recording(
    path, source, tracks, cars, targets, limit=None, stopped=None, definitions=None
):
    """Streaming local replay with finite retention, including explicitly requested laps."""
    replay = gt7_laps._Replay()
    replay.eng.track_db = tracks
    context = gt7_context.Store.for_recording(path).read()
    summaries, details = collections.OrderedDict(), collections.OrderedDict()
    previous = collections.OrderedDict()
    sizes = {}

    def drain(wall, final=False):
        while replay.laps:
            raw = replay.laps[0]
            if (
                not final
                and raw.get("gt7_time_s") is None
                and wall < raw["end"] + gt7_recording.GT7_TIME_WINDOW_S
                and len(replay.laps) <= PENDING_MAX
            ):
                break
            replay.laps.pop(0)
            replay.times._waiting = [
                p for p in replay.times._waiting if p[0] is not raw
            ]
            samples, tyres = replay.lap_samples.pop(0), replay.lap_tyres.pop(0)
            found = gt7_recording.session_tracks([raw], tracks).get(raw["session"])
            row = gt7_laps._indexed_lap(
                source,
                raw,
                samples,
                tyres,
                replay.first,
                tracks,
                cars,
                found,
                after_service=previous.get(raw["session"], (None, False))[1],
            )
            previous[raw["session"]] = (raw["lap"], raw["pit"])
            while len(previous) > SESSIONS_MAX:
                previous.popitem(last=False)
            row["measurement_hash"] = _measurement_hash(row)
            row = dict(
                gt7_context.annotate(context["data"], row),
                context_revision=context["revision"],
            )
            row = _decorate(row, context["data"], definitions, tracks)
            row.update(_start_wall=raw["start"], _end_wall=raw["end"])
            key = (row["session"], row["lap"])
            summaries[key] = _summary(row)
            while len(summaries) > SUMMARIES_MAX:
                summaries.popitem(last=False)
            size = len(json.dumps(row, allow_nan=False).encode("utf-8"))
            if size <= ENTRY_BYTES_MAX:
                details[key], sizes[key] = row, size
            while len(details) > DETAILS_MAX or sum(sizes.values()) > DETAIL_BYTES_MAX:

                def wanted(k):
                    return any(
                        k[1] == t[1]
                        and (
                            k[0] == t[0]
                            if t[2] is None
                            else abs(details[k]["_start_wall"] - t[2]) <= 0.2
                        )
                        for t in targets
                    )

                old = next((k for k in details if not wanted(k)), next(iter(details)))
                details.pop(old)
                sizes.pop(old)

    recording = gt7_recording.Recording(path)
    for wall, _kind, plain in recording.packets():
        if stopped and stopped():
            raise ValueError("Recovery stopped")
        if limit is not None and recording.pos > limit:
            break
        replay._feed(wall, gt7_telemetry.parse_packet(plain))
        if len(replay.cur) > SAMPLES_MAX:
            del replay.cur[SAMPLES_MAX:]
            replay.tail = None
            if replay.eng._acc is not None:
                replay.eng._acc._reject("Engineer active sample limit")
        drain(wall)
    drain(replay.last or 0, final=True)
    return {
        "laps": list(summaries.values()),
        "details": details,
        "requested": set(targets),
        "context_revision": context["revision"],
        "notes": context["data"].get("notes", []),
        "end_wall": replay.last,
    }


def _decorate(row, context, definitions, tracks):
    result = dict(row)
    result["notes"] = [
        note
        for note in context.get("notes", [])
        if note["scope"] == "recording"
        or note.get("session") == row.get("recording_session", row["session"])
        and (note["scope"] == "session" or note.get("lap") == row["lap"])
    ]
    if definitions is not None and tracks is not None:
        session = result["session"]
        measured = dict(result, session=result.get("recording_session", session))
        result = gt7_track_definitions.annotate_index(
            {"laps": [measured]}, context, definitions, tracks
        )["laps"][0]
        result["session"] = session
    return result


class Store:
    """All expensive work is shared. Readers receive copies of immutable publications."""

    def __init__(
        self,
        enabled,
        tracks=None,
        cars=None,
        runtime_base=None,
        bundled=None,
        clock=time.monotonic,
        queue_max=PACKETS_MAX,
        details_max=DETAILS_MAX,
        summaries_max=SUMMARIES_MAX,
        recording_source=None,
        context_source=None,
    ):
        self.generation = uuid.uuid4().hex
        self._enabled_source, self._clock = enabled, clock
        self._tracks, self._cars = tracks, cars
        self._pending_data = None
        self._runtime_base, self._bundled = runtime_base, bundled
        self._definitions = (
            gt7_track_definitions.Store(
                runtime_base,
                os.path.join(
                    bundled
                    or os.path.join(os.path.dirname(__file__), "..", "assets", "gt7"),
                    "track-definitions",
                ),
            )
            if runtime_base
            else None
        )
        self._definition_stamp = ()
        self._history_revision = 0
        self._history_hash = None
        self._queue = queue.Queue(maxsize=queue_max)
        self._recovery_queue = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._enabled = False
        self._source_replay = dict(active=False, label="", speed=1.0)
        self._epoch, self._sequence, self._revision = 0, 0, 0
        self._dropped, self._seen_drops = 0, 0
        self._details_max, self._summaries_max = details_max, summaries_max
        self._details, self._summaries = (
            collections.OrderedDict(),
            collections.OrderedDict(),
        )
        self._sessions = collections.OrderedDict()
        self._identities, self._session_keys = {}, {}
        self._sizes = {}
        self._closed_previous = collections.OrderedDict()
        self._receipt = None
        self._last_packet = None
        self._values = {}
        self._inputs = collections.deque()
        self._shifts = self._new_shifts()
        self._last_publish = None
        self._recording = None
        self._recording_current = recording_source is None
        self._recording_source, self._context_source = recording_source, context_source
        self._bindings = {}
        self._session_offset = 0
        self._recording_started = None
        self._mapping_known = True
        self._context_key = None
        self._recovery, self._tasks = collections.OrderedDict(), {}
        self._recovery_sizes = {}
        self._replays = 0
        self._context_revision = 0
        self._context = {"notes": [], "sessions": {}}
        self._context_error = None
        self._replay = self._new_replay()
        self._published = self._empty()
        self._refresh_definitions()
        self.refresh_enabled()
        self._worker = threading.Thread(
            target=self._run, name="engineer-live", daemon=True
        )
        self._reader = threading.Thread(
            target=self._recover, name="engineer-recovery", daemon=True
        )
        self._worker.start()
        self._reader.start()

    def _new_shifts(self):
        base = self._runtime_base
        reader = gt7_shifts.Cache(base).read if base else (lambda _car: None)
        return gt7_shifts.LiveObserver(reader)

    def _new_replay(self):
        replay = gt7_laps._Replay()
        replay.eng.track_db = self._tracks
        return replay

    def _empty(self):
        return {
            "version": 1,
            "generation": self.generation,
            "revision": self._revision,
            "context_revision": self._context_revision,
            "enabled": self._enabled,
            "session_id": None,
            "current": None,
            "instruments": {},
            "receipt": None,
            "sessions": [],
            "laps": [],
            "best_s": None,
            "shifts": None,
            "fuel_avg_l": None,
            "fuel_avg_laps": 0,
            "theoretical_s": None,
            "history": (
                "recording-backed"
                if self._recording and self._recording_current
                else "mixed"
                if self._bindings
                else "volatile"
            ),
            "warnings": [],
            "replay": dict(self._source_replay),
        }

    def refresh_enabled(self):
        """Worker-only in production; also an explicit synchronization point in tests."""
        try:
            enabled = self._enabled_source() is True
        except (OSError, ValueError, TypeError):
            enabled = False
        if enabled != self._enabled:
            self._enabled = enabled
            self._reset_generation()

    def _reset_generation(self, new_source=False):
        if new_source:
            self.generation = uuid.uuid4().hex
            self._recording = None
            self._recording_started = None
            self._recording_current = self._recording_source is None and not self._source_replay["active"]
        self._epoch += 1
        self._replay = self._new_replay()
        self._identities.clear()
        self._session_keys.clear()
        self._summaries.clear()
        self._details.clear()
        self._sessions.clear()
        self._sizes.clear()
        self._closed_previous.clear()
        self._bindings.clear()
        self._session_offset = 0
        self._mapping_known = self._recording is None
        self._context_key = None
        self._context = {"notes": [], "sessions": {}}
        self._context_revision = 0
        self._receipt = None
        self._last_packet = None
        self._values = {}
        self._shifts = self._new_shifts()
        with self._lock:
            self._inputs.clear()
        self._revision += 1
        with self._lock:
            self._published = self._empty()
            self._detail_publication = {}

    def reload_data(self, cars, tracks):
        """Called from the GT7 data watcher; the worker swaps the tables between packets."""
        self._pending_data = (cars, tracks)

    def _apply_data(self):
        pending, self._pending_data = self._pending_data, None
        if pending is not None:
            self._cars, self._tracks = pending
            self._replay.eng.track_db = self._tracks

    def reset(self, metadata, timeout=5):
        """Bound admission and acknowledgement; expired commands cannot publish later."""
        if self._stop.is_set():
            raise ValueError("Analysis worker is stopped")
        done, cancelled, failures = threading.Event(), threading.Event(), []
        end = time.monotonic() + timeout
        try:
            self._queue.put(("reset", dict(metadata), done, cancelled, failures), timeout=timeout)
        except queue.Full as exc:
            raise ValueError("Analysis reset queue is full") from exc
        if not done.wait(max(0, end - time.monotonic())):
            cancelled.set()
            raise ValueError("Analysis reset timed out")
        if failures:
            raise ValueError("Analysis reset failed")

    def put(self, pkt, wall_ts):
        """UDP path: no disk access, parsing, replay or blocking queue insertion."""
        if not self._enabled or self._stop.is_set() or not math.isfinite(wall_ts):
            return False
        self._last_packet = (wall_ts, self._clock())
        try:
            self._queue.put_nowait((self._epoch, pkt, wall_ts, self._last_packet[1]))
            return True
        except queue.Full:
            self._dropped += 1
            return False

    def _run(self):
        checked = time.monotonic()
        while not self._stop.is_set():
            if time.monotonic() - checked >= 0.2:
                self._apply_data()
                self.refresh_enabled()
                self._sync_recording()
                self._refresh_definitions()
                self._read_context()
                checked = time.monotonic()
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if not self._source_replay["active"] and self._receipt and self._replay.laps:
                    wall = self._receipt[1] + max(0.0, self._clock() - self._receipt[0])
                    if self._finish(wall):
                        self._publish()
                continue
            try:
                if item[0] == "reset":
                    try:
                        if not item[3].is_set():
                            self._source_replay = item[1]
                            self._reset_generation(new_source=True)
                    except (ValueError, TypeError, OSError, OverflowError):
                        item[4].append(True)
                        raise
                    finally:
                        item[2].set()
                elif item[0] == self._epoch and self._enabled:
                    self._consume(item)
            except (ValueError, TypeError, OSError, OverflowError):
                self._context_error = "Live preparation unavailable for this input"
            finally:
                self._queue.task_done()

    def _consume(self, item):
        _epoch, pkt, wall, mono = item
        replay = self._replay
        if (
            replay.first is None
            and self._recording
            and self._recording_started is not None
        ):
            self._mapping_known = abs(wall - self._recording_started) <= 0.2
            self._session_offset = 0
        replay._feed(wall, pkt)
        self._receipt = (
            mono,
            wall,
            "loading"
            if pkt.loading
            else "paused"
            if pkt.paused
            else "menu"
            if not pkt.on_track
            else "live",
        )
        self._values = instruments(pkt)
        self._record_inputs(pkt, mono)
        self._shifts.feed(pkt, wall, session=replay.eng.session)
        if self._dropped != self._seen_drops:
            if replay.eng._acc is not None:
                replay.eng._acc._reject("Engineer preparation queue overflow")
            self._seen_drops = self._dropped
        if len(replay.cur) > SAMPLES_MAX:
            del replay.cur[SAMPLES_MAX:]
            replay.tail = None
            if replay.eng._acc is not None:
                replay.eng._acc._reject("Engineer active sample limit")
        finished = self._finish(wall)
        if finished or self._last_publish is None or mono - self._last_publish >= 0.5:
            self._publish()
            self._last_publish = mono

    def _finish(self, wall):
        replay = self._replay
        finished = False
        while replay.laps:
            lap = replay.laps[0]
            if (
                lap.get("gt7_time_s") is None
                and wall < lap["end"] + gt7_recording.GT7_TIME_WINDOW_S
            ):
                if len(replay.laps) <= PENDING_MAX:
                    break
                replay.times._waiting = [
                    p for p in replay.times._waiting if p[0] is not lap
                ]
            samples = replay.lap_samples.pop(0)
            tyres = replay.lap_tyres.pop(0)
            replay.laps.pop(0)
            replay.times._waiting = [
                p for p in replay.times._waiting if p[0] is not lap
            ]
            found = gt7_recording.session_tracks([lap], self._tracks).get(
                lap["session"]
            )
            row = gt7_laps._indexed_lap(
                self.generation,
                lap,
                samples,
                tyres,
                replay.first,
                self._tracks,
                self._cars,
                found,
                after_service=self._closed_previous.get(lap["session"], (None, False))[
                    1
                ],
            )
            self._closed_previous[lap["session"]] = (lap["lap"], lap["pit"])
            while len(self._closed_previous) > SESSIONS_MAX:
                self._closed_previous.popitem(last=False)
            if row["gt7_time_s"] is None:
                row.update(time_s=None, pace_eligible=False, time_valid=False)
            row.update(
                state="confirmed" if row["gt7_time_s"] is not None else "unconfirmed"
            )
            row = self._identify(row, lap)
            if not self._mapping_known and self._recording and row["capture_complete"]:
                path, source = self._recording
                if not os.path.isfile(path) and path.endswith(".part"):
                    path = path[:-5]
                self.recording_detail(
                    source, path, row["recording_session"], row["lap"], identity=row
                )
            self._retain(row)
            finished = True
        return finished

    def _identify(self, row, raw):
        engine_session = raw["session"]
        key = (self._epoch, engine_session, row["car_id"])
        track = row.get("track_id")
        sid = self._session_keys.get(key)
        if sid is not None:
            previous = self._sessions.get(sid)
            if (
                previous
                and previous.get("track_id")
                and track
                and previous["track_id"] != track
            ):
                sid = None
        if sid is None:
            self._sequence += 1
            sid = f"{self.generation}:s{self._sequence}"
            self._session_keys[key] = sid
            self._sessions[sid] = {
                "id": sid,
                "car_id": row["car_id"],
                "car": row.get("car"),
                "track_id": track,
                "track": row.get("track", ""),
                "laps": 0,
            }
            self._trim_sessions()
        elif track:
            self._sessions[sid] = dict(
                self._sessions[sid], track_id=track, track=row.get("track", "")
            )
        self._sessions[sid]["car"] = row.get("car")
        lap_key = (self._epoch, engine_session, raw["lap"], raw["start"])
        lid = self._identities.get(lap_key)
        if lid is None:
            self._sequence += 1
            lid = f"{self.generation}:l{self._sequence}"
            self._identities[lap_key] = lid
        return dict(
            row,
            id=lid,
            session_id=sid,
            version=1,
            context_revision=self._context_revision,
            recording_session=engine_session - self._session_offset,
            _start_wall=raw["start"],
            _end_wall=raw["end"],
            _context_source=self._recording[1] if self._recording else "prepared",
        )

    def _annotate(self, row):
        measured = dict(row, session=row.get("recording_session", row["session"]))
        lookup = {
            (r.get("recording_session", r["session"]), r["lap"]): r
            for r in self._summaries.values()
            if r.get("_context_source") == row.get("_context_source")
        }
        lookup[(measured["session"], measured["lap"])] = measured
        data = self._context if self._mapping_known or self._recording is None else {}
        preceding = max(
            (n for s, n in lookup if s == measured["session"] and n < measured["lap"]),
            default=None,
        )
        result = dict(
            gt7_context.annotate(
                data, measured, lap_lookup=lookup, previous_lap=preceding
            ),
            session=row["session"],
            context_revision=self._context_revision if data is self._context else None,
        )
        if data is not self._context:
            result["context_warnings"].append("Recording session mapping unavailable")
        return _decorate(result, data, self._definitions, self._tracks)

    def _retain(self, row):
        row["measurement_hash"] = _measurement_hash(row)
        row = self._annotate(row)
        lid, sid = row["id"], row["session_id"]
        if (
            self._recording
            and self._recording_current
            and self._recording_started is not None
            and row["start_t_s"] + self._replay.first >= self._recording_started
        ):
            self._bindings[lid] = self._recording
        size = len(json.dumps(row, allow_nan=False).encode("utf-8"))
        self._summaries[lid] = dict(
            _summary(row),
            detail_state="ready" if size <= ENTRY_BYTES_MAX else "unavailable",
        )
        if size <= ENTRY_BYTES_MAX:
            self._details[lid], self._sizes[lid] = row, size
        self._sessions[sid] = dict(
            self._sessions[sid], laps=self._sessions[sid]["laps"] + 1
        )
        while len(self._summaries) > self._summaries_max:
            self._drop_summary(next(iter(self._summaries)))
        self._trim_details(sid)

    def _trim_details(self, sid):
        for lid, row in list(self._details.items()):
            size = len(json.dumps(row, allow_nan=False).encode("utf-8"))
            if size > ENTRY_BYTES_MAX:
                self._details.pop(lid)
                self._sizes.pop(lid, None)
                self._summaries[lid] = dict(
                    self._summaries[lid], detail_state="unavailable"
                )
            else:
                self._sizes[lid] = size
        while (
            len(self._details) > self._details_max
            or sum(self._sizes.values()) > DETAIL_BYTES_MAX
        ):
            old = next(
                (n for n, r in self._details.items() if r["session_id"] != sid),
                next(iter(self._details)),
            )
            del self._details[old]
            self._sizes.pop(old, None)
            if old in self._summaries:
                self._summaries[old] = dict(
                    self._summaries[old], detail_state="evicted"
                )

    def _drop_summary(self, lid):
        self._summaries.pop(lid, None)
        self._details.pop(lid, None)
        self._sizes.pop(lid, None)
        self._bindings.pop(lid, None)
        self._identities = {k: v for k, v in self._identities.items() if v != lid}

    def _trim_sessions(self):
        while len(self._sessions) > SESSIONS_MAX:
            expired, _ = self._sessions.popitem(last=False)
            for old in [
                n for n, r in self._summaries.items() if r["session_id"] == expired
            ]:
                self._drop_summary(old)
            self._session_keys = {
                k: v for k, v in self._session_keys.items() if v != expired
            }

    def _publish(self):
        replay = self._replay
        raw = replay.eng.open_lap_record()
        current = None
        if raw is not None and (replay.cur or replay.tail):
            raw["service_events"] = list(replay.cur_events)
            samples = replay.cur if replay.tail is None else replay.cur + [replay.tail]
            current = gt7_laps._indexed_lap(
                self.generation,
                raw,
                samples,
                replay.tyre,
                replay.first,
                self._tracks,
                self._cars,
                replay.eng.track,
                open_lap=True,
                after_service=self._closed_previous.get(raw["session"], (None, False))[
                    1
                ],
            )
            current.update(
                state="provisional",
                elapsed_s=raw["elapsed"],
                observed_from_line=replay.eng._acc.started_at_boundary,
                lap_role="pit"
                if raw["pit"]
                else "regular"
                if replay.eng._acc.started_at_boundary
                else "partial",
                reason="Current lap is provisional",
                reasons=list(raw["reasons"][:-1]),
            )
            current = self._annotate(self._identify(current, raw))
            current["provisional_comparison_eligible"] = bool(
                current["observed_from_line"]
                and raw["data_quality"] == "ok"
                and current["analysis_role"] not in ("pit", "partial", "warmup")
                and "Mixed fuel strategy within lap" not in current["context_warnings"]
                and "Service time outside recorded lap"
                not in current["context_warnings"]
            )
            current["pace_eligible"] = False
            current["comparison_eligible"] = False
        self._revision += 1
        doc = self._empty()
        sid = current["session_id"] if current else None
        pool = [
            r
            for r in self._details.values()
            if r["session_id"] == sid
            and r.get("comparison_eligible")
            and r["state"] == "confirmed"
            and gt7_context.compatible(current or {}, r)
        ]
        eligible = [
            r
            for r in self._summaries.values()
            if r["session_id"] == sid
            and r.get("comparison_eligible")
            and r["state"] == "confirmed"
            and gt7_context.compatible(current or {}, r)
        ]
        fuel_avg, fuel_laps = fuel_average(self._summaries.values(), sid)
        doc.update(
            revision=self._revision,
            current=current,
            session_id=sid,
            instruments=self._values,
            receipt=self._receipt,
            sessions=list(self._sessions.values()),
            laps=list(self._summaries.values()),
            best_s=min((r["time_s"] for r in eligible), default=None),
            shifts=self._shifts.snapshot(),
            fuel_avg_l=fuel_avg,
            fuel_avg_laps=fuel_laps,
            theoretical_s=gt7_laps.theoretical_best(pool),
            warnings=[self._context_error] if self._context_error else [],
        )
        digest = hashlib.sha256(
            json.dumps(
                [doc["sessions"], doc["laps"]], sort_keys=True, allow_nan=False
            ).encode()
        ).hexdigest()
        if digest != self._history_hash:
            self._history_revision += 1
            self._history_hash = digest
        doc["history_revision"] = self._history_revision
        with self._lock:
            self._published = doc
            self._detail_publication = dict(self._details)

    def _record_inputs(self, pkt, mono):
        with self._lock:
            if self._inputs and mono - self._inputs[-1][0] < INPUT_TRACE_DT * .9:  # packet jitter
                return
            self._inputs.append((mono, pkt.throttle / 255, pkt.brake / 255))
            while self._inputs[0][0] < mono - INPUT_TRACE_S:
                self._inputs.popleft()

    def inputs(self, since=None):
        """Recent throttle/brake samples (0..1) on the receipt clock, newest last."""
        with self._lock:
            rows = [r for r in self._inputs if since is None or round(r[0], 3) > since]
        return {
            "version": 1,
            "generation": self.generation,
            "samples": [
                {"t": round(t, 3), "throttle": round(thr, 3), "brake": round(brk, 3)}
                for t, thr, brk in rows
            ],
        }

    def snapshot(self, compact=False):
        with self._lock:
            doc = self._published
        if compact:
            doc = {k: v for k, v in doc.items() if k not in ("laps", "sessions")}
            if doc["current"]:
                doc = dict(doc, current=_summary(doc["current"]))
        result = copy.deepcopy(doc)
        receipt = doc["receipt"]
        packet = self._last_packet
        packet_age = max(0.0, self._clock() - packet[1]) if packet else None
        if receipt is None:
            result["receipt"] = {
                "age_s": None,
                "received_at": None,
                "state": "preparing" if packet else "waiting",
            }
        else:
            mono, wall, state = receipt
            age = max(0.0, self._clock() - mono)
            result["receipt"] = {
                "age_s": round(age, 3),
                "received_at": wall,
                "state": (
                    "stale"
                    if packet_age is None or packet_age >= STALE_S
                    else "delayed"
                    if age >= STALE_S
                    else state
                ),
            }
        result["receipt"].update(
            packet_age_s=round(packet_age, 3) if packet_age is not None else None,
            packet_received_at=packet[0] if packet else None,
        )
        recognisable = getattr(self._tracks, "recognisable", None)
        result["track_recognition"] = bool(recognisable()) if recognisable else False
        return result

    def history(self, since=None):
        with self._lock:
            doc = self._published
        revision = doc.get("history_revision", self._history_revision)
        result = dict(
            version=1,
            generation=self.generation,
            revision=revision,
            changed=since != revision,
        )
        if result["changed"]:
            result.update(
                laps=copy.deepcopy(doc["laps"]), sessions=copy.deepcopy(doc["sessions"])
            )
        return result

    def detail(self, lid):
        with self._lock:
            doc = self._published
            details = getattr(self, "_detail_publication", {})
        if doc["current"] and doc["current"]["id"] == lid:
            return {"state": "ready", "lap": copy.deepcopy(doc["current"])}
        row = details.get(lid)
        if row is not None:
            return {"state": "ready", "lap": copy.deepcopy(row)}
        brief = next((r for r in doc["laps"] if r["id"] == lid), None)
        if brief is None:
            return {"state": "unavailable", "lap": None}
        binding = self._bindings.get(lid)
        if binding is None:
            return {"state": brief["detail_state"], "lap": None}
        path, source = binding
        if not os.path.isfile(path) and path.endswith(".part"):
            path = path[:-5]
        return self.recording_detail(
            source, path, brief["recording_session"], brief["lap"], identity=brief
        )

    def bind_recording(self, path):
        """Local producer association, never a Console path selector."""
        source = gt7_context.source_identity(path)
        if (
            self._recording is None
            or self._recording_started is None
            or self._recording[1] != source
        ):
            self._session_offset = self._replay.eng.session - 1
            packet = next(gt7_recording.Recording(path).packets(), None)
            self._recording_started = packet[0] if packet else None
            self._mapping_known = (
                self._replay.first is None
                or self._recording_started is not None
                and self._recording_started >= self._replay.first
            )
            self._context_key = None
        self._recording = (os.path.realpath(path), source)
        if self._recording_started is not None and self._replay.first is not None:
            for lid, row in self._summaries.items():
                if row["start_t_s"] + self._replay.first >= self._recording_started:
                    self._bindings.setdefault(lid, self._recording)

    def _sync_recording(self):
        if self._source_replay["active"] or self._recording_source is None:
            return
        try:
            path = self._recording_source()
            self._recording_current = bool(path)
            if path:
                source = gt7_context.source_identity(path)
                if (
                    self._recording is None
                    or self._recording_started is None
                    or self._recording != (os.path.realpath(path), source)
                ):
                    self.bind_recording(path)
        except (OSError, ValueError, gt7_recording.RecordingError):
            pass  # The recorder may not have flushed its new header yet; try next tick.

    def _refresh_definitions(self):
        if not self._definitions or not self._tracks:
            return
        layouts = self._tracks.layouts() if hasattr(self._tracks, "layouts") else []
        stamps = []
        for identity in layouts[:4096]:
            for filename in (
                self._definitions.path(identity["id"], identity["reverse"]),
                os.path.join(
                    self._definitions.bundled,
                    os.path.basename(
                        self._definitions.path(identity["id"], identity["reverse"])
                    ),
                ),
            ):
                try:
                    stat = os.stat(filename)
                    stamps.append((
                        filename,
                        stat.st_dev,
                        stat.st_ino,
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                    ))
                except OSError:
                    stamps.append((filename, None, None, None, None, None))
        self._definition_stamp = tuple(stamps)

    def _record_stamp(self, source, path):
        stamp = _record_stamp(source, path)
        return stamp[:6] + (stamp[6] + (self._definition_stamp,),)

    def recording_detail(self, source, path, session, lap, identity=None):
        """Caller must authorize source on every read, including pending/cached reads."""
        try:
            if gt7_context.source_identity(path) != source:
                return {"state": "unavailable", "lap": None}
            stamp = self._record_stamp(source, path)
        except (OSError, ValueError, gt7_recording.RecordingError):
            return {"state": "unavailable", "lap": None}
        start = identity.get("_start_wall") if identity else None
        target = (session, lap, start)
        with self._lock:
            cached = self._cached_recording(stamp, path)
            if cached is not None:
                row = (
                    next(
                        (
                            r
                            for r in cached["details"].values()
                            if r["lap"] == lap and abs(r["_start_wall"] - start) <= 0.2
                        ),
                        None,
                    )
                    if start is not None
                    else cached["details"].get((session, lap))
                )
                if row is None and target in cached["requested"]:
                    if not (identity and path.endswith(".part")):
                        return {"state": "unavailable", "lap": None}
                    if (
                        cached["end_wall"] is not None
                        and identity.get("_end_wall") is not None
                        and cached["end_wall"]
                        > identity["_end_wall"] + gt7_recording.GT7_TIME_WINDOW_S + 2
                    ):
                        return {"state": "unavailable", "lap": None}
                    if stamp[4] == cached["prefix_size"]:
                        return {"state": "pending", "lap": None}
                if row is not None:
                    if identity and identity.get("measurement_hash") != row.get(
                        "measurement_hash"
                    ):
                        incomplete = (
                            row.get("gt7_time_s") is None
                            and cached["end_wall"] is not None
                            and cached["end_wall"]
                            <= row["_end_wall"] + gt7_recording.GT7_TIME_WINDOW_S + 2
                        )
                        if not (path.endswith(".part") and incomplete):
                            return {"state": "unavailable", "lap": None}
                        if stamp[4] == cached["prefix_size"]:
                            return {"state": "pending", "lap": None}
                        row = None
                if row is not None:
                    row = copy.deepcopy(row)
                    if identity is not None:
                        row.update({
                            k: identity[k]
                            for k in (
                                "id",
                                "session_id",
                                "version",
                                "state",
                                "recording_session",
                                "session",
                            )
                        })
                        row["rec"] = self.generation
                    return {
                        "state": "ready",
                        "lap": row,
                        "context_revision": cached["context_revision"],
                    }
            running = next(
                (
                    key
                    for key in self._tasks
                    if path.endswith(".part")
                    and key[:4] == stamp[:4]
                    and key[6] == stamp[6]
                ),
                stamp,
            )
            if running != stamp:
                if len(self._tasks[running]) < DETAILS_MAX:
                    self._tasks[running].add(target)
                    return {"state": "pending", "lap": None}
                return {"state": "unavailable", "lap": None}
            if stamp not in self._tasks:
                try:
                    self._recovery_queue.put_nowait((stamp, path))
                    self._tasks[stamp] = {target}
                except queue.Full:
                    return {"state": "unavailable", "lap": None}
            elif len(self._tasks[stamp]) < DETAILS_MAX:
                self._tasks[stamp].add(target)
            else:
                return {"state": "unavailable", "lap": None}
        return {"state": "pending", "lap": None}

    def _cached_recording(self, stamp, path):
        """Under _lock. Closed files use exact signatures; active files keep frozen prefixes."""
        if not path.endswith(".part"):
            return self._recovery.get(stamp)
        for key, cached in reversed(list(self._recovery.items())):
            if key[:4] != stamp[:4] or key[6] != stamp[6]:
                continue
            if (
                stamp[4] < cached["checked_size"]
                or stamp[4] == cached["checked_size"]
                and stamp[5] != cached["checked_mtime"]
            ):
                self._recovery.pop(key)
                self._recovery_sizes.pop(key, None)
                continue
            cached["checked_size"], cached["checked_mtime"] = stamp[4], stamp[5]
            return cached
        return None

    def recording_snapshot(self, source, path):
        """Bounded summaries, prepared once per current source/context signature."""
        try:
            if gt7_context.source_identity(path) != source:
                return {"state": "unavailable", "laps": []}
            stamp = self._record_stamp(source, path)
        except (OSError, ValueError, gt7_recording.RecordingError):
            return {"state": "unavailable", "laps": []}
        with self._lock:
            cached = self._cached_recording(stamp, path)
        if cached is None:
            status = self.recording_detail(source, path, 0, 0)
            return {"state": status["state"], "laps": []}
        return {
            "state": "ready",
            "laps": copy.deepcopy(cached["laps"]),
            "notes": copy.deepcopy(cached["notes"]),
            "context_revision": cached["context_revision"],
        }

    def _recover(self):
        while not self._stop.is_set():
            try:
                stamp, path = self._recovery_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._replays += 1
                with self._lock:
                    targets = set(self._tasks[stamp])
                digest = _prefix_digest(path, stamp[4], self._stop.is_set)
                idx = _bounded_recording(
                    path,
                    stamp[0],
                    self._tracks,
                    self._cars,
                    targets,
                    limit=stamp[4],
                    stopped=self._stop.is_set,
                    definitions=self._definitions,
                )
                size = (
                    sum(
                        len(json.dumps(v).encode("utf-8"))
                        for v in idx["details"].values()
                    )
                    + len(json.dumps(idx["laps"]).encode("utf-8"))
                    + len(json.dumps(idx["notes"]).encode("utf-8"))
                )
                now = self._record_stamp(stamp[0], path)
                unchanged = now == stamp
                appended = (
                    path.endswith(".part")
                    and now[:4] == stamp[:4]
                    and now[6] == stamp[6]
                    and now[4] >= stamp[4]
                    and _prefix_digest(path, stamp[4], self._stop.is_set) == digest
                )
                idx.update(
                    prefix_size=stamp[4],
                    prefix_digest=digest,
                    checked_size=now[4],
                    checked_mtime=now[5],
                )
                if (
                    gt7_context.source_identity(path) == stamp[0]
                    and (unchanged or appended)
                    and size <= DETAIL_BYTES_MAX
                ):
                    with self._lock:
                        self._recovery[stamp] = idx
                        self._recovery_sizes[stamp] = size
                        while (
                            len(self._recovery) > RECOVERY_MAX
                            or sum(self._recovery_sizes.values()) > DETAIL_BYTES_MAX
                        ):
                            old, _ = self._recovery.popitem(last=False)
                            self._recovery_sizes.pop(old, None)
            except (OSError, ValueError, TypeError, gt7_recording.RecordingError):
                pass  # Missing/invalid local sources remain pending or unavailable to readers.
            finally:
                with self._lock:
                    self._tasks.pop(stamp, None)
                self._recovery_queue.task_done()

    def _read_context(self):
        if self._source_replay["active"]:
            return
        if self._recording is None and self._context_source is None:
            return
        try:
            path = self._recording[0] if self._recording else None
            if path and not os.path.isfile(path) and path.endswith(".part"):
                path = path[:-5]
            doc = (
                gt7_context.Store.for_recording(path).read()
                if path
                else self._context_source()
            )
            expected = self._recording[1] if self._recording else "prepared"
            if doc["source_id"] != expected:
                raise ValueError("Context source identity changed")
            if not self._mapping_known:
                with self._lock:
                    recovered = [
                        cache
                        for stamp, cache in self._recovery.items()
                        if stamp[0] == expected
                    ]
                for current in reversed(list(self._summaries.values())):
                    match = next(
                        (
                            row
                            for cache in recovered
                            for row in cache["details"].values()
                            if row["lap"] == current["lap"]
                            and abs(row["_start_wall"] - current["_start_wall"]) <= 0.2
                        ),
                        None,
                    )
                    if match is not None:
                        self._session_offset = current["session"] - match["session"]
                        self._mapping_known = True
                        self._context_key = None
                        self._details = collections.OrderedDict(
                            (
                                lid,
                                dict(
                                    row,
                                    recording_session=row["session"]
                                    - self._session_offset,
                                )
                                if row.get("_context_source") == expected
                                else row,
                            )
                            for lid, row in self._details.items()
                        )
                        self._summaries = collections.OrderedDict(
                            (
                                lid,
                                dict(
                                    row,
                                    recording_session=row["session"]
                                    - self._session_offset,
                                )
                                if row.get("_context_source") == expected
                                else row,
                            )
                            for lid, row in self._summaries.items()
                        )
                        break
            key = (doc["source_id"], doc["revision"], self._definition_stamp)
            self._context_error = None
            if key != self._context_key:
                self._context_key = key
                self._context, self._context_revision = doc["data"], doc["revision"]
                self._details = collections.OrderedDict(
                    (
                        lid,
                        self._annotate(row)
                        if row.get("_context_source") == key[0]
                        else row,
                    )
                    for lid, row in self._details.items()
                )
                self._summaries = collections.OrderedDict(
                    (
                        lid,
                        _summary(self._annotate(row))
                        if row.get("_context_source") == key[0]
                        else row,
                    )
                    for lid, row in self._summaries.items()
                )
                self._trim_details(self._published["session_id"])
                self._publish()
        except (OSError, ValueError, TypeError, gt7_recording.RecordingError):
            error = "Context unavailable; comparison conditions unconfirmed"
            if self._context_error != error:
                self._context, self._context_revision = (
                    {"notes": [], "sessions": {}},
                    None,
                )
                self._context_key = None
                self._context_error = error
                self._details = collections.OrderedDict(
                    (lid, self._annotate(row)) for lid, row in self._details.items()
                )
                self._summaries = collections.OrderedDict(
                    (lid, _summary(self._annotate(row)))
                    for lid, row in self._summaries.items()
                )
                self._trim_details(self._published["session_id"])
                self._publish()

    def flush(self, timeout=5):
        end = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.005)
        if self._queue.unfinished_tasks:
            return False
        self._publish()
        return True

    def wait_recovery(self, timeout=5):
        end = time.monotonic() + timeout
        while self._recovery_queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.005)
        return not self._recovery_queue.unfinished_tasks

    def stats(self):
        return {
            "details": len(self._details),
            "summaries": len(self._summaries),
            "sessions": len(self._sessions),
            "samples": len(self._replay.cur),
            "dropped": self._dropped,
            "replays": self._replays,
            "detail_bytes": sum(self._sizes.values()),
            "recovery_bytes": sum(self._recovery_sizes.values()),
        }

    def close(self):
        if getattr(self, "api", None) is not None:
            self.api.close()
        self._stop.set()
        self._worker.join(timeout=5)
        self._reader.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

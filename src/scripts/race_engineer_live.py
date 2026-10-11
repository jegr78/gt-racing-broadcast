"""One bounded live analysis worker and one shared local recording recovery worker."""
import collections
import copy
import json
import math
import os
import queue
import threading
import time
import uuid

import gt7_context
import gt7_laps
import gt7_recording
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
SUMMARY_FIELDS = frozenset(('id', 'session_id', 'version', 'context_revision',
    'recording_session', 'session', 'lap', 'state', 'detail_state', 'gt7_time_s',
    'time_s', 'relay_time_s', 'status', 'reason', 'car_id', 'car', 'track_id', 'track',
    'layout', 'distance_m', 'length_m', 'fuel_used_l', 'top_speed_kmh',
    'capture_complete', 'trace_complete', 'time_valid', 'data_quality', 'reasons',
    'pace_eligible', 'comparison_eligible', 'analysis_role', 'compound',
    'context_confirmed', 'context_warnings', 'tyre_age_laps', 'start_t_s', 'end_t_s'))


def _summary(row):
    return {k: v for k, v in row.items() if k in SUMMARY_FIELDS}


def _finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def instruments(pkt):
    """Only measured channels; a missing/nonfinite channel stays unavailable."""
    return {'speed_kmh': _finite(pkt.speed_mps * 3.6), 'gear': _finite(pkt.gear),
            'rpm': _finite(pkt.rpm), 'throttle': _finite(pkt.throttle / 255 * 100),
            'brake': _finite(pkt.brake / 255 * 100), 'fuel_l': _finite(pkt.fuel_level),
            'fuel_capacity_l': _finite(pkt.fuel_capacity),
            'tyres_c': [_finite(v) for v in pkt.tyre_temp],
            'steer_rad': _finite(pkt.steer_rad)}


def _record_stamp(source, path):
    st = os.stat(path)
    context_path = gt7_context._stem(path) + '.context.json'
    try:
        ctx = os.stat(context_path)
        context_stamp = (ctx.st_size, ctx.st_mtime_ns)
    except FileNotFoundError:
        context_stamp = (0, 0)
    return source, os.path.realpath(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, context_stamp


def _bounded_recording(path, source, tracks, cars, targets):
    """Streaming local replay with finite retention, including explicitly requested laps."""
    replay = gt7_laps._Replay()
    replay.eng.track_db = tracks
    context = gt7_context.Store.for_recording(path).read()
    summaries, details = collections.OrderedDict(), collections.OrderedDict()
    sizes = {}
    def drain(wall, final=False):
        while replay.laps:
            raw = replay.laps[0]
            if not final and raw.get('gt7_time_s') is None and wall < raw['end'] + gt7_recording.GT7_TIME_WINDOW_S and len(replay.laps) <= PENDING_MAX:
                break
            replay.laps.pop(0)
            replay.times._waiting = [p for p in replay.times._waiting if p[0] is not raw]
            samples, tyres = replay.lap_samples.pop(0), replay.lap_tyres.pop(0)
            found = gt7_recording.session_tracks([raw], tracks).get(raw['session'])
            row = gt7_laps._indexed_lap(source, raw, samples, tyres, replay.first, tracks, cars, found)
            row = dict(gt7_context.annotate(context['data'], row), context_revision=context['revision'])
            key = (row['session'], row['lap'])
            summaries[key] = _summary(row)
            while len(summaries) > SUMMARIES_MAX:
                summaries.popitem(last=False)
            size = len(json.dumps(row, allow_nan=False).encode('utf-8'))
            if size <= ENTRY_BYTES_MAX:
                details[key], sizes[key] = row, size
            while len(details) > DETAILS_MAX or sum(sizes.values()) > DETAIL_BYTES_MAX:
                old = next((k for k in details if k not in targets), next(iter(details)))
                details.pop(old)
                sizes.pop(old)
    for wall, _kind, plain in gt7_recording.Recording(path).packets():
        replay._feed(wall, gt7_telemetry.parse_packet(plain))
        if len(replay.cur) > SAMPLES_MAX:
            del replay.cur[SAMPLES_MAX:]
            replay.tail = None
            if replay.eng._acc is not None:
                replay.eng._acc._reject('Engineer active sample limit')
        drain(wall)
    drain(replay.last or 0, final=True)
    return {'laps': list(summaries.values()), 'details': details,
            'requested': set(targets), 'context_revision': context['revision'],
            'notes': context['data'].get('notes', [])}


class Store:
    """All expensive work is shared. Readers receive copies of immutable publications."""
    def __init__(self, enabled, tracks=None, cars=None, runtime_base=None, bundled=None,
                 clock=time.monotonic, queue_max=PACKETS_MAX, details_max=DETAILS_MAX,
                 summaries_max=SUMMARIES_MAX):
        self.generation = uuid.uuid4().hex
        self._enabled_source, self._clock = enabled, clock
        self._tracks, self._cars = tracks, cars
        self._runtime_base, self._bundled = runtime_base, bundled
        self._queue = queue.Queue(maxsize=queue_max)
        self._recovery_queue = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._enabled = False
        self._epoch, self._sequence, self._revision = 0, 0, 0
        self._dropped, self._seen_drops = 0, 0
        self._details_max, self._summaries_max = details_max, summaries_max
        self._details, self._summaries = collections.OrderedDict(), collections.OrderedDict()
        self._sessions = collections.OrderedDict()
        self._identities, self._session_keys = {}, {}
        self._sizes = {}
        self._receipt = None
        self._values = {}
        self._last_publish = None
        self._recording = None
        self._recovery, self._tasks = collections.OrderedDict(), {}
        self._recovery_sizes = {}
        self._replays = 0
        self._context_revision = 0
        self._context = {'notes': [], 'sessions': {}}
        self._context_error = None
        self._replay = self._new_replay()
        self._published = self._empty()
        self.refresh_enabled()
        self._worker = threading.Thread(target=self._run, name='engineer-live', daemon=True)
        self._reader = threading.Thread(target=self._recover, name='engineer-recovery', daemon=True)
        self._worker.start()
        self._reader.start()

    def _new_replay(self):
        replay = gt7_laps._Replay()
        replay.eng.track_db = self._tracks
        return replay

    def _empty(self):
        return {'version': 1, 'generation': self.generation, 'revision': self._revision,
                'context_revision': self._context_revision, 'enabled': self._enabled,
                'session_id': None, 'current': None, 'instruments': {}, 'receipt': None,
                'sessions': [], 'laps': [], 'best_s': None, 'theoretical_s': None,
                'history': 'recording-backed' if self._recording else 'volatile',
                'warnings': []}

    def refresh_enabled(self):
        """Worker-only in production; also an explicit synchronization point in tests."""
        try:
            enabled = self._enabled_source() is True
        except (OSError, ValueError, TypeError):
            enabled = False
        if enabled != self._enabled:
            self._enabled = enabled
            self._epoch += 1
            self._replay = self._new_replay()
            self._identities.clear()
            self._session_keys.clear()
            self._summaries.clear()
            self._details.clear()
            self._sessions.clear()
            self._sizes.clear()
            self._receipt = None
            self._values = {}
            self._revision += 1
            with self._lock:
                self._published = self._empty()
                self._detail_publication = {}

    def put(self, pkt, wall_ts):
        """UDP path: no disk access, parsing, replay or blocking queue insertion."""
        if not self._enabled or self._stop.is_set() or not math.isfinite(wall_ts):
            return False
        try:
            self._queue.put_nowait((self._epoch, pkt, wall_ts, self._clock()))
            return True
        except queue.Full:
            self._dropped += 1
            return False

    def _run(self):
        checked = time.monotonic()
        while not self._stop.is_set():
            if time.monotonic() - checked >= .2:
                self.refresh_enabled()
                self._read_context()
                checked = time.monotonic()
            try:
                item = self._queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                if item[0] == self._epoch and self._enabled:
                    self._consume(item)
            except (ValueError, TypeError, OSError, OverflowError):
                self._context_error = 'Live preparation unavailable for this input'
            finally:
                self._queue.task_done()

    def _consume(self, item):
        _epoch, pkt, wall, mono = item
        replay = self._replay
        replay._feed(wall, pkt)
        self._receipt = (mono, wall, 'loading' if pkt.loading else 'paused' if pkt.paused
                         else 'menu' if not pkt.on_track else 'live')
        self._values = instruments(pkt)
        if self._dropped != self._seen_drops:
            if replay.eng._acc is not None:
                replay.eng._acc._reject('Engineer preparation queue overflow')
            self._seen_drops = self._dropped
        if len(replay.cur) > SAMPLES_MAX:
            del replay.cur[SAMPLES_MAX:]
            replay.tail = None
            if replay.eng._acc is not None:
                replay.eng._acc._reject('Engineer active sample limit')
        finished = False
        while replay.laps:
            lap = replay.laps[0]
            if lap.get('gt7_time_s') is None and wall < lap['end'] + gt7_recording.GT7_TIME_WINDOW_S:
                if len(replay.laps) <= PENDING_MAX:
                    break
                replay.times._waiting = [p for p in replay.times._waiting if p[0] is not lap]
            samples = replay.lap_samples.pop(0)
            tyres = replay.lap_tyres.pop(0)
            replay.laps.pop(0)
            replay.times._waiting = [p for p in replay.times._waiting if p[0] is not lap]
            found = gt7_recording.session_tracks([lap], self._tracks).get(lap['session'])
            row = gt7_laps._indexed_lap(self.generation, lap, samples, tyres, replay.first,
                                       self._tracks, self._cars, found)
            if row['gt7_time_s'] is None:
                row.update(time_s=None, pace_eligible=False, time_valid=False)
            row.update(state='confirmed' if row['gt7_time_s'] is not None else 'unconfirmed')
            self._retain(self._identify(row, lap))
            finished = True
        if finished or self._last_publish is None or mono - self._last_publish >= .5:
            self._publish()
            self._last_publish = mono

    def _identify(self, row, raw):
        engine_session = raw['session']
        key = (self._epoch, engine_session, row['car_id'])
        track = row.get('track_id')
        sid = self._session_keys.get(key)
        if sid is not None:
            previous = self._sessions.get(sid)
            if previous and previous.get('track_id') and track and previous['track_id'] != track:
                sid = None
        if sid is None:
            self._sequence += 1
            sid = f'{self.generation}:s{self._sequence}'
            self._session_keys[key] = sid
            self._sessions[sid] = {'id': sid, 'car_id': row['car_id'], 'track_id': track,
                                   'track': row.get('track', ''), 'laps': 0}
            self._trim_sessions()
        elif track:
            self._sessions[sid] = dict(self._sessions[sid], track_id=track, track=row.get('track', ''))
        lap_key = (self._epoch, engine_session, raw['lap'], raw['start'])
        lid = self._identities.get(lap_key)
        if lid is None:
            self._sequence += 1
            lid = f'{self.generation}:l{self._sequence}'
            self._identities[lap_key] = lid
        return dict(row, id=lid, session_id=sid, version=1,
                    context_revision=self._context_revision,
                    recording_session=engine_session)

    def _annotate(self, row):
        return dict(gt7_context.annotate(self._context, row),
                    context_revision=self._context_revision)

    def _retain(self, row):
        row = self._annotate(row)
        lid, sid = row['id'], row['session_id']
        size = len(json.dumps(row, allow_nan=False).encode('utf-8'))
        self._summaries[lid] = dict(_summary(row), detail_state='ready' if size <= ENTRY_BYTES_MAX else 'unavailable')
        if size <= ENTRY_BYTES_MAX:
            self._details[lid], self._sizes[lid] = row, size
        self._sessions[sid] = dict(self._sessions[sid], laps=self._sessions[sid]['laps'] + 1)
        while len(self._summaries) > self._summaries_max:
            self._drop_summary(next(iter(self._summaries)))
        while len(self._details) > self._details_max or sum(self._sizes.values()) > DETAIL_BYTES_MAX:
            old = next((n for n, r in self._details.items() if r['session_id'] != sid), next(iter(self._details)))
            del self._details[old]
            self._sizes.pop(old, None)
            if old in self._summaries:
                self._summaries[old] = dict(self._summaries[old], detail_state='evicted')

    def _drop_summary(self, lid):
        self._summaries.pop(lid, None)
        self._details.pop(lid, None)
        self._sizes.pop(lid, None)
        self._identities = {k: v for k, v in self._identities.items() if v != lid}

    def _trim_sessions(self):
        while len(self._sessions) > SESSIONS_MAX:
            expired, _ = self._sessions.popitem(last=False)
            for old in [n for n, r in self._summaries.items() if r['session_id'] == expired]:
                self._drop_summary(old)
            self._session_keys = {k: v for k, v in self._session_keys.items() if v != expired}

    def _publish(self):
        replay = self._replay
        raw = replay.eng.open_lap_record()
        current = None
        if raw is not None and (replay.cur or replay.tail):
            samples = replay.cur if replay.tail is None else replay.cur + [replay.tail]
            current = gt7_laps._indexed_lap(self.generation, raw, samples, replay.tyre,
                                           replay.first, self._tracks, self._cars,
                                           replay.eng.track, open_lap=True)
            current = self._annotate(self._identify(dict(current, state='provisional'), raw))
            current['pace_eligible'] = False
            current['comparison_eligible'] = False
        self._revision += 1
        doc = self._empty()
        sid = current['session_id'] if current else None
        pool = [r for r in self._details.values() if r['session_id'] == sid
                and r.get('comparison_eligible') and r['state'] == 'confirmed']
        eligible = [r for r in self._summaries.values() if r['session_id'] == sid
                    and r.get('comparison_eligible') and r['state'] == 'confirmed']
        doc.update(revision=self._revision, current=current, session_id=sid,
                   instruments=self._values, receipt=self._receipt,
                   sessions=list(self._sessions.values()), laps=list(self._summaries.values()),
                   best_s=min((r['time_s'] for r in eligible), default=None),
                   theoretical_s=gt7_laps.theoretical_best(pool),
                   warnings=[self._context_error] if self._context_error else [])
        with self._lock:
            self._published = doc
            self._detail_publication = dict(self._details)

    def snapshot(self):
        with self._lock:
            doc = self._published
        result = copy.deepcopy(doc)
        receipt = doc['receipt']
        if receipt is None:
            result['receipt'] = {'age_s': None, 'received_at': None, 'state': 'waiting'}
        else:
            mono, wall, state = receipt
            age = max(0.0, self._clock() - mono)
            result['receipt'] = {'age_s': round(age, 3), 'received_at': wall,
                                 'state': 'stale' if age >= STALE_S else state}
        return result

    def detail(self, lid):
        with self._lock:
            doc = self._published
            details = getattr(self, '_detail_publication', {})
        if doc['current'] and doc['current']['id'] == lid:
            return {'state': 'ready', 'lap': copy.deepcopy(doc['current'])}
        row = details.get(lid)
        if row is not None:
            return {'state': 'ready', 'lap': copy.deepcopy(row)}
        brief = next((r for r in doc['laps'] if r['id'] == lid), None)
        if brief is None:
            return {'state': 'unavailable', 'lap': None}
        if self._recording is None:
            return {'state': brief['detail_state'], 'lap': None}
        path, source = self._recording
        return self.recording_detail(source, path, brief['recording_session'], brief['lap'], identity=brief)

    def bind_recording(self, path):
        """Local producer association, never a Console path selector."""
        self._recording = (os.path.realpath(path), gt7_context.source_identity(path))

    def recording_detail(self, source, path, session, lap, identity=None):
        """Caller must authorize source on every read, including pending/cached reads."""
        try:
            if gt7_context.source_identity(path) != source:
                return {'state': 'unavailable', 'lap': None}
            stamp = _record_stamp(source, path)
        except (OSError, ValueError, gt7_recording.RecordingError):
            return {'state': 'unavailable', 'lap': None}
        with self._lock:
            cached = self._recovery.get(stamp)
            if cached is not None:
                row = cached['details'].get((session, lap))
                if row is None and (session, lap) in cached['requested']:
                    return {'state': 'unavailable', 'lap': None}
                if row is not None:
                    row = copy.deepcopy(row)
                    if identity is not None:
                        row.update({k: identity[k] for k in ('id', 'session_id', 'version', 'state', 'recording_session')})
                    return {'state': 'ready', 'lap': row, 'context_revision': cached['context_revision']}
            if stamp not in self._tasks:
                try:
                    self._recovery_queue.put_nowait((stamp, path))
                    self._tasks[stamp] = {(session, lap)}
                except queue.Full:
                    return {'state': 'unavailable', 'lap': None}
            elif len(self._tasks[stamp]) < DETAILS_MAX:
                self._tasks[stamp].add((session, lap))
            else:
                return {'state': 'unavailable', 'lap': None}
        return {'state': 'pending', 'lap': None}

    def recording_snapshot(self, source, path):
        """Bounded summaries, prepared once per current source/context signature."""
        try:
            if gt7_context.source_identity(path) != source:
                return {'state': 'unavailable', 'laps': []}
            stamp = _record_stamp(source, path)
        except (OSError, ValueError, gt7_recording.RecordingError):
            return {'state': 'unavailable', 'laps': []}
        with self._lock:
            cached = self._recovery.get(stamp)
        if cached is None:
            status = self.recording_detail(source, path, 0, 0)
            return {'state': status['state'], 'laps': []}
        return {'state': 'ready', 'laps': copy.deepcopy(cached['laps']),
                'notes': copy.deepcopy(cached['notes']), 'context_revision': cached['context_revision']}

    def _recover(self):
        while not self._stop.is_set():
            try:
                stamp, path = self._recovery_queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                self._replays += 1
                with self._lock:
                    targets = set(self._tasks[stamp])
                idx = _bounded_recording(path, stamp[0], self._tracks, self._cars, targets)
                size = (sum(len(json.dumps(v).encode('utf-8')) for v in idx['details'].values())
                        + len(json.dumps(idx['laps']).encode('utf-8'))
                        + len(json.dumps(idx['notes']).encode('utf-8')))
                if (gt7_context.source_identity(path) == stamp[0] and _record_stamp(stamp[0], path) == stamp
                        and size <= DETAIL_BYTES_MAX):
                    with self._lock:
                        self._recovery[stamp] = idx
                        self._recovery_sizes[stamp] = size
                        while len(self._recovery) > RECOVERY_MAX or sum(self._recovery_sizes.values()) > DETAIL_BYTES_MAX:
                            old, _ = self._recovery.popitem(last=False)
                            self._recovery_sizes.pop(old, None)
            except (OSError, ValueError, TypeError, gt7_recording.RecordingError):
                pass
            finally:
                with self._lock:
                    self._tasks.pop(stamp, None)
                self._recovery_queue.task_done()

    def _read_context(self):
        if self._recording is None:
            return
        try:
            doc = gt7_context.Store.for_recording(self._recording[0]).read()
            if doc['revision'] != self._context_revision:
                self._context, self._context_revision = doc['data'], doc['revision']
                self._details = collections.OrderedDict((lid, self._annotate(row)) for lid, row in self._details.items())
                self._summaries = collections.OrderedDict((lid, self._annotate(row)) for lid, row in self._summaries.items())
                self._publish()
            self._context_error = None
        except (OSError, ValueError, TypeError, gt7_recording.RecordingError):
            error = 'Context unavailable; comparison conditions unconfirmed'
            if self._context_error != error:
                self._context, self._context_revision = {'notes': [], 'sessions': {}}, None
                self._context_error = error
                self._details = collections.OrderedDict((lid, self._annotate(row)) for lid, row in self._details.items())
                self._summaries = collections.OrderedDict((lid, _summary(self._annotate(row))) for lid, row in self._summaries.items())
                self._publish()

    def flush(self, timeout=5):
        end = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(.005)
        if self._queue.unfinished_tasks:
            return False
        self._publish()
        return True

    def wait_recovery(self, timeout=5):
        end = time.monotonic() + timeout
        while self._recovery_queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(.005)
        return not self._recovery_queue.unfinished_tasks

    def stats(self):
        return {'details': len(self._details), 'summaries': len(self._summaries),
                'sessions': len(self._sessions), 'samples': len(self._replay.cur),
                'dropped': self._dropped, 'replays': self._replays}

    def close(self):
        self._stop.set()
        self._worker.join(timeout=5)
        self._reader.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

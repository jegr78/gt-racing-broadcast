#!/usr/bin/env python3
"""Optional, versioned telemetry context. Raw captures and rebuilt indexes stay immutable.

Profile templates/preparation are separate from recording-owned context and history.
All callers receive copies; files are bounded, validated and replaced atomically.
"""
import copy
import contextlib
import gzip
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time

FORMAT = 'racecast-telemetry-context'
VERSION = 1
MAX_BYTES = 1024 * 1024
MAX_ITEMS = 2000
_LOCK = threading.RLock()
_HELD = threading.local()
_TOKEN = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
COMPOUNDS = ('CH', 'CM', 'CS', 'SH', 'SM', 'SS', 'RH', 'RM', 'RS', 'IM', 'W', 'D', 'S')
NUMBERS = {'laps', 'duration_min', 'pit_stops', 'tyre_x', 'fuel_x', 'refuel_lps',
           'time_progression', 'starting_fuel_l'}
BOOLEANS = {'bop', 'fixed_setup', 'starting_fuel_fixed'}
TEXT = {'title', 'weather', 'time_of_day', 'pit_window', 'test_goal', 'conditions_note'}
ROLES = {'regular', 'first', 'pit', 'warmup', 'unknown'}


class Conflict(ValueError):
    """The caller edited a stale revision; preserve the saved version."""


def _number(v, label, integer=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        raise ValueError(label + ' must be a finite nonnegative number')
    if integer and int(v) != v:
        raise ValueError(label + ' must be an integer')
    return int(v) if integer else v


def _text(v, limit=16000):
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError('text is missing or too long')
    return v


def _token(v):
    if not isinstance(v, str) or not _TOKEN.fullmatch(v):
        raise ValueError('invalid identifier')
    return v


def _object(v):
    if not isinstance(v, dict):
        raise ValueError('expected an object')
    return v


def _list(v):
    if not isinstance(v, list) or len(v) > MAX_ITEMS:
        raise ValueError('expected a bounded list')
    return v


def _keys(v, allowed):
    if set(v) - set(allowed):
        raise ValueError('unsupported context field')


def _bounded(v):
    try:
        raw = json.dumps(v, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('context must contain finite JSON values') from exc
    if len(raw) > MAX_BYTES:
        raise ValueError('context is too large')
    return raw


def validate_settings(settings):
    settings = copy.deepcopy(_object(settings))
    _keys(settings, NUMBERS | BOOLEANS | TEXT | {'start_type', 'pit_rule', 'required_tyres'})
    for k, v in settings.items():
        if v is None:
            continue  # missing is never silently zero/false
        if k in NUMBERS:
            _number(v, k, k in {'laps', 'pit_stops'})
        elif k in BOOLEANS:
            if not isinstance(v, bool):
                raise ValueError(k + ' must be true, false or unknown')
        elif k in TEXT:
            _text(v, 2000)
        elif k == 'required_tyres':
            if any(t not in COMPOUNDS for t in _list(v)):
                raise ValueError('unsupported tyre compound')
        elif v not in ({'rolling', 'standing', 'unknown'} if k == 'start_type'
                       else {'minimum', 'exact', 'maximum', 'unknown'}):
            raise ValueError('unsupported race rule')
    return settings


def _strategy(v):
    v = copy.deepcopy(_object(v))
    _keys(v, {'fuel_map', 'shortshift', 'targets', 'note'})
    if v.get('fuel_map') is not None:
        _number(v['fuel_map'], 'fuel map', True)
        if v['fuel_map'] not in range(1, 7):
            raise ValueError('fuel map must be 1 to 6 or unknown')
    if v.get('shortshift') is not None and not isinstance(v['shortshift'], bool):
        raise ValueError('shortshift must be true, false or unknown')
    if 'note' in v:
        _text(v['note'], 2000)
    for gear, rpm in _object(v.get('targets', {})).items():
        if gear not in [str(i) for i in range(1, 16)]:
            raise ValueError('invalid target gear')
        _number(rpm, 'target RPM')
    return v


def validate_data(data):
    data = copy.deepcopy(_object(data))
    _bounded(data)
    _keys(data, {'notes', 'sessions', 'track_definition'})
    notes = _list(data.setdefault('notes', []))
    sessions = _object(data.setdefault('sessions', {}))
    if len(sessions) > MAX_ITEMS:
        raise ValueError('too many sessions')
    ids = set()
    for n in notes:
        _object(n)
        _keys(n, {'id', 'scope', 'session', 'lap', 'text', 'position'})
        ident = _token(n.get('id'))
        if ident in ids:
            raise ValueError('duplicate note identifier')
        ids.add(ident)
        if n.get('scope') not in {'recording', 'session', 'lap'}:
            raise ValueError('invalid note scope')
        _text(n.get('text'))
        if n['scope'] != 'recording':
            _number(n.get('session'), 'note session', True)
            if n['session'] < 1:
                raise ValueError('note session must be positive')
        if n['scope'] == 'lap':
            _number(n.get('lap'), 'note lap', True)
        if n.get('position') is not None:
            pos = _object(n['position'])
            if n['scope'] != 'lap' or not {'x', 'z'} <= pos.keys():
                raise ValueError('location notes require world X/Z and a source lap')
            _keys(pos, {'x', 'y', 'z', 'd', 't', 'reference_version'})
            for v in pos.values():
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError('note position must be finite')
    for key, session in sessions.items():
        if not isinstance(key, str) or not key.isdigit() or str(int(key)) != key or int(key) < 1:
            raise ValueError('invalid session identifier')
        _object(session)
        _keys(session, {'settings', 'confirmed', 'stints', 'lap_roles', 'origin', 'label',
                        'shift_reference'})
        session['settings'] = validate_settings(session.get('settings', {}))
        if not isinstance(session.setdefault('confirmed', False), bool):
            raise ValueError('confirmation must be true or false')
        if 'label' in session:
            _text(session['label'], 200)
        stints = _list(session.setdefault('stints', []))
        stint_ids = set()
        for stint in stints:
            _object(stint)
            _keys(stint, {'id', 'start_lap', 'start_recording_s', 'compound', 'confirmed',
                         'tyre_service', 'refuel', 'refuel_l', 'warmup_laps', 'strategy', 'strategy_changes', 'note'})
            ident = _token(stint.get('id'))
            if ident in stint_ids:
                raise ValueError('duplicate stint identifier')
            stint_ids.add(ident)
            _number(stint.get('start_lap'), 'stint start lap', True)
            if stint.get('start_recording_s') is not None:
                _number(stint['start_recording_s'], 'service time')
            if stint.get('compound') is not None and stint['compound'] not in COMPOUNDS:
                raise ValueError('unsupported stint compound')
            if stint.get('refuel_l') is not None:
                _number(stint['refuel_l'], 'manual refuel amount')
            for k in ('confirmed', 'tyre_service', 'refuel'):
                if stint.get(k) is not None and not isinstance(stint[k], bool):
                    raise ValueError(k + ' must be true, false or unknown')
            _number(stint.get('warmup_laps', 1), 'warmup laps', True)
            stint['strategy'] = _strategy(stint.get('strategy', {}))
            if 'note' in stint:
                _text(stint['note'])
            for change in _list(stint.get('strategy_changes', [])):
                _object(change)
                _keys(change, {'lap', 'recording_s', 'strategy'})
                _number(change.get('lap'), 'strategy-change lap', True)
                if change.get('recording_s') is not None:
                    _number(change['recording_s'], 'strategy-change time')
                change['strategy'] = _strategy(change.get('strategy', {}))
        for lap, role in _object(session.setdefault('lap_roles', {})).items():
            if not isinstance(lap, str) or not lap.isdigit() or role not in ROLES:
                raise ValueError('invalid lap role')
        if 'origin' in session:
            origin = _object(session['origin'])
            _keys(origin, {'kind', 'template', 'session'})
            if origin.get('kind') not in {'manual', 'template', 'copied'}:
                raise ValueError('invalid context origin')
        if session.get('shift_reference') is not None:
            _object(session['shift_reference'])  # interpretation belongs to the shift-reference module
    if data.get('track_definition') is not None:
        _object(data['track_definition'])  # bounded snapshot; track module validates its geometry
    return data


@contextlib.contextmanager
def _file_lock(path):
    """OS-released advisory lock; reentrant in the current thread, shared across processes."""
    held = getattr(_HELD, 'paths', set())
    if path in held:
        yield
        return
    directory = os.path.dirname(os.path.abspath(path))
    if os.path.islink(directory):
        raise ValueError('context directory cannot be a symlink')
    os.makedirs(directory, exist_ok=True)
    lockpath = path + '.lock'
    if os.path.islink(lockpath):
        raise ValueError('context lock cannot be a symlink')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(lockpath, flags, 0o600)
    acquired = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b' ')
        deadline = time.monotonic() + 2.0
        while not acquired:
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise Conflict('context is busy; retry the edit') from exc
                time.sleep(.02)
        _HELD.paths = held | {path}
        yield
    finally:
        _HELD.paths = held
        if acquired:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _load(path, compressed=False):
    if os.path.islink(path):
        raise ValueError('context cannot be a symlink')
    try:
        with (gzip.open(path, 'rb') if compressed else open(path, 'rb')) as f:
            raw = f.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_BYTES:
        raise ValueError('context is too large')
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError('context is not readable JSON') from exc


def _atomic(path, doc, compressed=False):
    raw = _bounded(doc)
    directory = os.path.dirname(os.path.abspath(path))
    if os.path.islink(directory):
        raise ValueError('context directory cannot be a symlink')
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.context-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(gzip.compress(raw) if compressed else raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _stem(path):
    name = str(path)
    if name.endswith('.part'):
        name = name[:-5]
    if name.endswith('.gt7rec'):
        name = name[:-7]
    return name


def _profile_root(recdir):
    recdir = os.path.abspath(recdir)
    root = os.path.dirname(recdir) if os.path.basename(recdir) == 'telemetry-recordings' else recdir
    return os.path.realpath(root)


def source_identity(path):
    """Stable across .part finalization and file copies; legacy captures use their first record."""
    import gt7_recording
    rec = gt7_recording.Recording(path)
    identity = rec.header.get('recording_id')
    if isinstance(identity, str) and re.fullmatch('[0-9a-f]{32}', identity):
        return identity
    with open(path, 'rb') as f:
        head = f.read(rec.header_end)
        record = f.read(gt7_recording._REC.size)
        if len(record) == gt7_recording._REC.size:
            _ts, _kind, length = gt7_recording._REC.unpack(record)
            record += f.read(length)
    return hashlib.sha256(head + record).hexdigest()


def _context_envelope(doc, source_id):
    """Validate metadata before exposing it as JSON or resolving a historical snapshot."""
    _object(doc)
    _bounded(doc)
    if doc.get('format') != FORMAT or _number(doc.get('version'), 'version', True) != VERSION:
        raise ValueError('unsupported context format/version')
    if doc.get('source_id') != source_id:
        raise ValueError('context belongs to another recording')
    doc['revision'] = _number(doc.get('revision'), 'revision', True)
    if doc.get('updated_at') is not None:
        _number(doc['updated_at'], 'context timestamp')
    if 'operation' in doc:
        _text(doc['operation'], 200)
    _object(doc.get('draft', {}))
    return doc


class Store:
    def __init__(self, path, source_id, capture_path=None):
        self.path = os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(path))), os.path.basename(path))
        self.source_id = source_id
        self.capture_path = os.path.abspath(_stem(capture_path) + ".gt7rec") if capture_path else None
        self.history_dir = self.path[:-5] + '-history'

    @classmethod
    def for_recording(cls, path):
        return cls(_stem(path) + '.context.json', source_identity(path), path)

    @classmethod
    def prepared(cls, recdir):
        return cls(os.path.join(_profile_root(recdir), 'telemetry-prepared.json'), 'prepared')

    def read(self):
        with _LOCK:
            doc = _load(self.path)
            if doc is None:
                return {'format': FORMAT, 'version': VERSION, 'source_id': self.source_id,
                        'revision': 0, 'updated_at': None, 'data': {'notes': [], 'sessions': {}},
                        'draft': {}}
            _context_envelope(doc, self.source_id)
            doc['data'] = validate_data(doc.get('data'))
            return copy.deepcopy(doc)

    def _check_capture(self):
        if self.capture_path is None:
            return
        path = self.capture_path if os.path.isfile(self.capture_path) else self.capture_path + '.part'
        if not os.path.isfile(path):
            raise ValueError('recording no longer available')
        if source_identity(path) != self.source_id:
            raise ValueError('recording identity changed')

    def save(self, data, expected, draft=None, operation='edit'):
        _number(expected, 'expected revision', True)
        self._check_capture()
        with _LOCK, _file_lock(self.path):
            self._check_capture()
            current = self.read()
            if expected != current['revision']:
                raise Conflict('context changed elsewhere; reload before saving')
            normalized = validate_data(data)
            draft = copy.deepcopy(_object(draft or {}))
            _bounded(draft)
            changed = normalized != current['data']
            doc = dict(current, data=normalized, draft=draft,
                       revision=current['revision'] + int(changed), updated_at=time.time(),
                       operation=operation)
            _bounded(doc)
            if changed:
                self._archive(current)
            _atomic(self.path, doc)
            return copy.deepcopy(doc)

    def _archive(self, doc):
        _atomic(os.path.join(self.history_dir, str(doc['revision']) + '.json.gz'), doc, compressed=True)

    def history(self, limit=50, before=None):
        with _LOCK:
            if not os.path.isdir(self.history_dir):
                return []
            versions = sorted((int(n[:-8]) for n in os.listdir(self.history_dir)
                               if n.endswith('.json.gz') and n[:-8].isdigit()), reverse=True)
            if before is not None:
                versions = [v for v in versions if v < before]
            out = []
            for revision in versions[:min(100, max(1, limit))]:
                doc = _load(os.path.join(self.history_dir, str(revision) + '.json.gz'), compressed=True)
                if doc is not None:
                    _context_envelope(doc, self.source_id)
                    if doc['revision'] != revision:
                        raise ValueError('history revision does not match its file')
                    out.append({'revision': revision, 'updated_at': doc.get('updated_at'),
                                'operation': doc.get('operation', 'edit')})
            return out

    def restore(self, revision, expected):
        revision = _number(revision, 'revision', True)
        with _LOCK:
            previous = _load(os.path.join(self.history_dir, str(revision) + '.json.gz'), compressed=True)
            if previous is None:
                raise ValueError('no matching context revision')
            _context_envelope(previous, self.source_id)
            if previous['revision'] != revision:
                raise ValueError('history revision does not match its file')
            return self.save(previous['data'], expected, operation='restore ' + str(revision))

    def export(self):
        doc = self.read()
        return {k: v for k, v in doc.items() if k not in {'draft', 'operation'}}


def templates(recdir):
    with _LOCK:
        doc = _load(os.path.join(_profile_root(recdir), 'telemetry-templates.json'))
        if doc is None:
            return {'version': 1, 'templates': {}}
        if not isinstance(doc, dict) or doc.get('version') != 1:
            raise ValueError('unsupported template format')
        values = _object(doc.get('templates'))
        if len(values) > MAX_ITEMS:
            raise ValueError('too many templates')
        for key, template in values.items():
            _object(template)
            _token(key)
            _text(template.get('name'), 200)
            validate_settings(template.get('settings'))
        return copy.deepcopy(doc)


def save_template(recdir, key, name, settings):
    with _LOCK, _file_lock(os.path.join(_profile_root(recdir), 'telemetry-templates.json')):
        doc = templates(recdir)
        doc['templates'][_token(key)] = {'name': _text(name, 200),
                                       'settings': validate_settings(settings)}
        _atomic(os.path.join(_profile_root(recdir), 'telemetry-templates.json'), doc)
        return doc


def delete_template(recdir, key):
    with _LOCK, _file_lock(os.path.join(_profile_root(recdir), 'telemetry-templates.json')):
        doc = templates(recdir)
        doc['templates'].pop(_token(key), None)
        _atomic(os.path.join(_profile_root(recdir), 'telemetry-templates.json'), doc)
        return doc


def apply_template(data, template, session=1):
    result = validate_data(data)
    target = result['sessions'].setdefault(str(session), {})
    target['settings'] = validate_settings(template['settings'])
    target['confirmed'] = False  # choosing a template does not confirm its current applicability
    target['origin'] = {'kind': 'template'}
    return validate_data(result)


def copy_session(data, source, target):
    result = validate_data(data)
    previous = result['sessions'].get(str(source))
    if previous is None:
        raise ValueError('no source session')
    result['sessions'][str(target)] = {'settings': copy.deepcopy(previous['settings']),
                                      'confirmed': False, 'stints': [],
                                      'origin': {'kind': 'copied', 'session': source}}
    return validate_data(result)


def attach_prepared(path, recdir, identity):
    """Runs independently of the packet writer; a preparation is consumed once."""
    prep = Store.prepared(recdir)
    initial = prep.read()
    if not initial['data']['notes'] and not initial['data']['sessions']:
        return False
    with _LOCK, _file_lock(prep.path):
        doc = prep.read()
        if not doc['data']['notes'] and not doc['data']['sessions']:
            return False
        data = copy.deepcopy(doc['data'])
        for key, session in data['sessions'].items():
            if key != '1':
                session['confirmed'] = False
                for stint in session.get('stints', []):
                    stint['confirmed'] = False
        Store(_stem(path) + '.context.json', identity, path).save(data, expected=0)
        prep.save({}, expected=doc['revision'], draft=doc.get('draft', {}))
        return True


def delete_recording(path):
    """Only recording-owned files; shared templates and track definitions are untouched."""
    import shutil
    stem = _stem(path)
    context = stem + '.context.json'
    with _LOCK, _file_lock(context):
        try:
            os.remove(context)
        except FileNotFoundError:
            pass  # metadata may never have been created
        history = context[:-5] + '-history'
        if os.path.isdir(history):
            shutil.rmtree(history)
    # With no capture remaining, queued writers fail identity validation before writing.
    if not any(os.path.exists(p) for p in (stem + '.gt7rec', stem + '.gt7rec.part')):
        try:
            os.remove(context + '.lock')
        except FileNotFoundError:
            pass  # metadata may never have been created


def _memberships(stints, lap):
    """Intersect declared service boundaries with this recorded lap, retaining unknown times."""
    n, start, end = lap['lap'], lap.get('start_t_s'), lap.get('end_t_s')
    before = [s for s in stints if s['start_lap'] < n]
    active = before[-1] if before else None
    boundaries = []
    warnings = []
    for i, stint in enumerate(stints):
        if stint['start_lap'] != n:
            continue
        t = stint.get('start_recording_s')
        if t is not None and start is not None and end is not None and not start <= t <= end:
            warnings.append('Service time outside recorded lap')
            t = None
        if not i or (t is not None and start is not None and t <= start):
            active = stint
        else:
            boundaries.append((stint, t))
    out = []
    def member(stint, lo, hi):
        if lo is not None and hi is not None and lo == hi:
            return
        strategy = copy.deepcopy(stint.get('strategy', {})) if stint and stint.get('confirmed') else {}
        changes = sorted(stint.get('strategy_changes', []),
                         key=lambda c: (c['lap'], c.get('recording_s') or -1)) if stint and stint.get('confirmed') else []
        def append(segment_lo, segment_hi):
            out.append({'stint_id': stint.get('id') if stint else None,
                        'compound': stint.get('compound') if stint and stint.get('confirmed') else None,
                        'confirmed': bool(stint and stint.get('confirmed')),
                        'start_t_s': segment_lo, 'end_t_s': segment_hi,
                        'strategy': copy.deepcopy(strategy)})
        for change in changes:
            t = change.get('recording_s')
            if change['lap'] < n or (change['lap'] == n and (t is None or lo is not None and t <= lo)):
                strategy.update(change['strategy'])
            elif change['lap'] == n and t is not None and lo is not None and hi is not None:
                if start is not None and end is not None and not start <= t <= end:
                    warnings.append('Strategy time outside recorded lap')
                elif lo < t < hi:
                    append(lo, t)
                    lo = t
                    strategy.update(change['strategy'])
        append(lo, hi)
    lo = start
    for stint, t in boundaries:
        member(active, lo, t)
        active, lo = stint, t
    member(active, lo, end)
    return out, list(dict.fromkeys(warnings)), bool(boundaries)


def _strategy_conflicts(strategies):
    """Only contradictory known driving settings count; unknowns and free notes do not."""
    for key in ('fuel_map', 'shortshift'):
        values = [s[key] for s in strategies if s.get(key) is not None]
        if values and any(v != values[0] for v in values[1:]):
            return True
    gears = {gear for s in strategies for gear in s.get('targets', {})}
    for gear in gears:
        values = [s['targets'][gear] for s in strategies if gear in s.get('targets', {})]
        if values and any(v != values[0] for v in values[1:]):
            return True
    return False


def annotate(data, lap):
    """Context overlay: retain original measured fields, add analysis roles and provenance."""
    result = dict(lap)
    session = data.get('sessions', {}).get(str(lap['session']), {})
    stints = sorted(session.get('stints', []),
                    key=lambda s: (s['start_lap'], s.get('start_recording_s') or -1))
    n = lap['lap']
    prior = [s for s in stints if s['start_lap'] <= n]
    stint = prior[-1] if prior else None
    memberships, warnings, service_in_lap = _memberships(stints, lap)
    compounds = list(dict.fromkeys(m['compound'] for m in memberships if m['compound']))
    compound = compounds[0] if len(compounds) == 1 and all(m['compound'] for m in memberships) else None
    role = session.get('lap_roles', {}).get(str(n), lap.get('lap_role', 'regular'))
    if role == 'unknown':
        role = lap.get('lap_role', 'regular')
    if service_in_lap and all(m['confirmed'] for m in memberships):
        if lap.get('lap_role') != 'pit':
            warnings.append('Manual service has no matching capture inference')
        if str(n) not in session.get('lap_roles', {}):
            role = 'pit'
    if stint and stint.get('confirmed') and stint.get('tyre_service') and role == 'regular':
        first_full = stint['start_lap'] if stints and stint is stints[0] else stint['start_lap'] + 1
        if first_full <= n < first_full + stint.get('warmup_laps', 1):
            role = 'warmup'
    strategies = []
    for m in memberships:
        if m['strategy'] not in strategies:
            strategies.append(m['strategy'])
    strategy = strategies[0] if len(strategies) == 1 else {}
    strategy_conflict = _strategy_conflicts(strategies)
    if strategy_conflict:
        warnings.append('Mixed fuel strategy within lap')
    confirmed = bool(session.get('confirmed') and compound and all(m['confirmed'] for m in memberships)
                     and not warnings)
    if not session.get('confirmed'):
        warnings.append('Race settings unconfirmed')
    if compound is None:
        warnings.append('Mixed compounds during service' if len(compounds) > 1 else 'Tyre compound unconfirmed')
    if lap.get('after_service') and not (stint and stint.get('tyre_service')):
        warnings.append('Inferred service has no confirmed tyre assignment')
    tyre_stints = [s for s in prior if s.get('confirmed') and
                   (s.get('tyre_service') or stints and s is stints[0])]
    result.update(compounds=compounds, compound=compound, memberships=memberships,
                  context_confirmed=confirmed, context_warnings=list(dict.fromkeys(warnings)),
                  analysis_role=role, strategy=strategy, strategies=strategies,
                  context_settings=copy.deepcopy(session.get('settings', {})),
                  settings_confirmed=bool(session.get('confirmed')),
                  stint_id=stint.get('id') if stint else None,
                  tyre_age_laps=n - tyre_stints[-1]['start_lap'] if tyre_stints and compound else None,
                  comparison_eligible=bool(lap.get('pace_eligible', lap.get('status') in ('counted', 'reference'))
                                           and role not in {'first', 'pit', 'warmup', 'partial'}
                                           and not strategy_conflict))
    return result


def compatible(reference, candidate):
    """Known contradictions prevent default comparison; unknowns remain explicitly unknown."""
    if not candidate.get('comparison_eligible'):
        return False
    if reference.get('compound') and candidate.get('compound') != reference['compound']:
        return False
    sa = reference.get('context_settings', {}) if reference.get('settings_confirmed', True) else {}
    sb = candidate.get('context_settings', {}) if candidate.get('settings_confirmed', True) else {}
    if any(sa.get(k) is not None and sb.get(k) is not None and sa[k] != sb[k]
           for k in ('bop', 'fixed_setup', 'tyre_x', 'fuel_x', 'time_progression', 'time_of_day')):
        return False
    a = reference.get('strategies') or [reference.get('strategy', {})]
    b = candidate.get('strategies') or [candidate.get('strategy', {})]
    return not _strategy_conflicts(a + b)

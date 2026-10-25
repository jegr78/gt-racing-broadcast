"""Explicit training calibration with frozen personal evidence and current grants."""

import collections
import copy
import hashlib
import json
import math
import uuid
from dataclasses import dataclass

import gt7_context

LAP_BYTES_MAX = 1024 * 1024
SEEN_MAX = 128
SESSION_MAX = 64
OBJECTIVES = ('balanced', 'consistency', 'pace')
MODES = ('practice', 'time_trial')
CONTEXT = ('session_id', 'car_id', 'track_id', 'layout', 'compound', 'stint_id',
           'strategy', 'strategies', 'context_settings', 'settings_confirmed',
           'definition_fingerprint', 'sector_variant_id')
LAP_FIELDS = frozenset(CONTEXT + (
    'id', 'lap', 'time_s', 'gt7_time_s', 'state', 'time_valid', 'comparison_eligible',
    'capture_complete', 'trace_complete', 'data_quality', 'analysis_role', 'lap_role',
    'after_service', 'context_revision', 'context_confirmed', 'context_warnings',
    'measurement_hash', 'version', 'recording_id', 'recording_session', '_context_source',
    'trace', 'points', 'sectors', 'length_m', 'distance_m', 'tyre_age_laps',
    'corner_metrics', 'driver_channels', 'track_definition_warnings'))


class CoachError(ValueError):
    def __init__(self, code, message='Coach operation is unavailable'):
        self.code = code
        super().__init__(message)


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 160 and all(ord(c) >= 32 for c in value)


def eligible(lap):
    try:
        valid_time = type(lap.get('time_s')) in (int, float) and math.isfinite(lap['time_s']) and lap['time_s'] > 0
    except OverflowError:
        valid_time = False
    return (lap.get('state') == 'confirmed' and lap.get('comparison_eligible') is True
            and lap.get('capture_complete') is True and lap.get('trace_complete') is True
            and lap.get('time_valid') is True and lap.get('data_quality') == 'ok'
            and lap.get('analysis_role') == 'regular'
            and not lap.get('after_service')
            and valid_time)


@dataclass(frozen=True)
class _Lap:
    row: dict
    digest: str

    def public(self):
        return {k: copy.deepcopy(self.row.get(k)) for k in (
            'id', 'lap', 'session_id', 'time_s', 'car_id', 'track_id', 'layout',
            'compound', 'context_revision', 'definition_fingerprint')}


def _prepare(value):
    if not isinstance(value, dict) or not eligible(value):
        return None
    row = {k: v for k, v in value.items() if k in LAP_FIELDS}
    if (not _text(row.get('id')) or not _text(row.get('session_id'))
            or type(row.get('lap')) is not int or not 0 < row['lap'] <= 1000000
            or row.get('car_id') is not None and (type(row['car_id']) is not int or row['car_id'] <= 0)
            or any(row.get(k) is not None and not _text(row[k]) for k in (
                'track_id', 'layout', 'stint_id', 'definition_fingerprint'))
            or row.get('compound') is not None and row['compound'] not in gt7_context.COMPOUNDS
            or any(not isinstance(row.get(k, {}), dict) for k in ('strategy', 'context_settings'))
            or not isinstance(row.get('strategies', []), list)
            or len(row.get('strategies', [])) > 32
            or any(not isinstance(s, dict) for s in row.get('strategies', []))):
        return None
    try:
        encoded = json.dumps(row, sort_keys=True, allow_nan=False).encode()
        if len(encoded) > LAP_BYTES_MAX:
            return None
        def strategy(value):
            declared = gt7_context._strategy(value)
            return {k: v for k, v in declared.items() if k in ('fuel_map', 'shortshift', 'targets')
                    and v is not None and not (k == 'targets' and not v)}
        row['strategy'] = strategy(row.get('strategy', {}))
        strategies = [strategy(s) for s in row.get('strategies') or [row['strategy']]]
        if gt7_context._strategy_conflicts(strategies + [row['strategy']]):
            return None
        row['strategies'] = [json.loads(s) for s in sorted({json.dumps(s, sort_keys=True) for s in strategies})]
        settings = gt7_context.validate_settings(row.get('context_settings', {}))
        if type(row.get('settings_confirmed', False)) is not bool:
            return None
        row['settings_confirmed'] = row.get('settings_confirmed', False)
        row['context_settings'] = {k: v for k, v in settings.items() if k in (
            'bop', 'fixed_setup', 'tyre_x', 'fuel_x', 'time_progression', 'time_of_day')
            and v is not None} if row['settings_confirmed'] else {}
        # A metadata revision alone does not change measured/contextual facts.
        facts = {k: v for k, v in row.items() if k not in ('context_revision', 'version', 'context_warnings')}
        digest = hashlib.sha256(json.dumps(facts, sort_keys=True, allow_nan=False).encode()).hexdigest()
        return _Lap(copy.deepcopy(row), digest)
    except (TypeError, ValueError, RecursionError, OverflowError):
        return None


def _context(lap):
    return {k: copy.deepcopy(lap.get(k)) for k in CONTEXT}


def _compatible(own, other):
    if any(own.get(k) != other.get(k) for k in (
            'car_id', 'track_id', 'layout', 'definition_fingerprint', 'sector_variant_id')):
        return False
    if own.get('session_id') != other.get('session_id') and not (
            own.get('car_id') and own.get('track_id') and own.get('layout')):
        return False
    if (own.get('session_id') == other.get('session_id')
            and own.get('stint_id') != other.get('stint_id')):
        return False
    return gt7_context.compatible(own, other) and gt7_context.compatible(other, own)


def _authorized(authorize):
    try:
        return callable(authorize) and authorize() is True
    except Exception:
        # A broken permission reader must revoke rather than retain private data.
        return False


class Calibration:
    """Trusted, serialized caller owns training identity and sends current context."""

    def __init__(self):
        self.active = False
        self.objective = 'balanced'
        self.owner = None
        self.mode = None
        self.training_id = None
        self.epoch = uuid.uuid4().hex
        self._phase = 'inactive'
        self._events = collections.deque(maxlen=16)
        self._retired = set()
        self._clear()

    def _clear(self):
        self._cohort = []
        self._baseline = None
        self._working = self._candidate = None
        self._grant = None
        self._selected = False
        self._focus = False
        self._seen = collections.OrderedDict()
        self._latest = 0
        self._context = None
        self._accepted = False
        self._reason = None

    def start(self, owner, mode='practice', objective='balanced'):
        if mode not in MODES:
            raise CoachError('invalid_mode', 'Training requires practice or time trial')
        if objective not in OBJECTIVES:
            raise CoachError('invalid_objective', 'Choose Balanced, Consistency or Pace')
        if (not isinstance(owner, dict) or set(owner) != {'profile', 'subject'}
                or not all(_text(v) for v in owner.values())):
            raise CoachError('invalid_owner')
        self._clear()
        self._events.clear()
        self._retired.clear()
        self.owner = copy.deepcopy(owner)
        self.mode, self.objective = mode, objective
        self.training_id = uuid.uuid4().hex
        self.epoch = uuid.uuid4().hex
        self.active, self._phase = True, 'calibrating'
        return self.snapshot()

    def invalidate(self, reason='context_changed'):
        if not _text(reason):
            raise CoachError('invalid_reason')
        self._events.append(dict(reason=reason, epoch=self.epoch,
                                 baseline_id=self._baseline['id'] if self._baseline else None))
        self._clear()
        self.epoch = uuid.uuid4().hex
        if self.active:
            self._phase = 'calibrating'
        return self.snapshot()

    def set_objective(self, objective):
        if objective not in OBJECTIVES:
            raise CoachError('invalid_objective')
        if objective != self.objective:
            self.objective = objective
            self.invalidate('objective_changed')
        return self.snapshot()

    def _check_grant(self):
        if self._selected and not _authorized(self._grant):
            self._selected, self._grant, self._focus = False, None, False
            self._working = self._candidate
            self.epoch = uuid.uuid4().hex
            self._reason = 'reference_revoked'

    def ingest(self, value):
        self._accepted = False
        self._check_grant()
        if not self.active:
            return self.snapshot()
        session_id = value.get('session_id') if isinstance(value, dict) else None
        if isinstance(session_id, str) and session_id in self._retired:
            self._reason = 'retired_session'
            return self.snapshot()
        entry = _prepare(value)
        if entry is None:
            ident = value.get('id') if isinstance(value, dict) else None
            retained = self._cohort + [e for e in (self._working, self._candidate) if e]
            if (isinstance(ident, str) and isinstance(session_id, str)
                    and ((session_id, ident) in self._seen or any(
                        e.row['id'] == ident and e.row['session_id'] == session_id for e in retained))):
                self.invalidate('evidence_corrected')
            self._reason = 'unsuitable'
            return self.snapshot()
        row = entry.row
        retained = self._cohort + [e for e in (self._working, self._candidate) if e]
        key = (row['session_id'], row['id'])
        previous = self._seen.get(key) or next(
            (e.digest for e in retained if (e.row['session_id'], e.row['id']) == key), None)
        if previous is not None:
            if previous == entry.digest:
                self._reason = 'duplicate'
                return self.snapshot()
            self.invalidate('evidence_corrected')
        elif (self._context is not None and row['session_id'] == self._context['session_id']
              and row['lap'] <= self._latest):
            self._reason = 'out_of_order'
            return self.snapshot()
        elif self._context is not None and _context(row) != self._context:
            if row['session_id'] != self._context['session_id']:
                if len(self._retired) >= SESSION_MAX:
                    self.stop()
                    self._reason = 'session_limit'
                    return self.snapshot()
                self._retired.add(self._context['session_id'])
            self.invalidate('context_changed')
        self._context = _context(row)
        self._latest = row['lap']
        self._seen[key] = entry.digest
        while len(self._seen) > SEEN_MAX:
            self._seen.popitem(last=False)
        if len(self._cohort) < 3:
            self._cohort.append(entry)
        if self._candidate is None or row['time_s'] < self._candidate.row['time_s']:
            self._candidate = entry
        if not self._focus and not self._selected:
            self._working = self._candidate
        if self._baseline is None and len(self._cohort) >= 2:
            low, high = sorted(e.row['time_s'] for e in self._cohort[:2])
            self._baseline = dict(id=uuid.uuid4().hex,
                                  lap_ids=[e.row['id'] for e in self._cohort[:2]],
                                  time_s=low + (high - low) / 2)
        self._phase = 'ready' if len(self._cohort) >= 3 else 'provisional' if self._baseline else 'calibrating'
        self._accepted, self._reason = True, None
        return self.snapshot()

    def begin_focus(self):
        self._check_grant()
        if not self.active or self._baseline is None:
            raise CoachError('calibration_required')
        self._focus = True
        return self.snapshot()

    def end_focus(self):
        self._focus = False
        if not self._selected and self._working is not self._candidate:
            self._working = self._candidate
            self.epoch = uuid.uuid4().hex
        return self.snapshot()

    def select_reference(self, value, authorize=None):
        if not self.active:
            raise CoachError('training_required')
        if value is None:
            self._selected, self._grant, self._focus = False, None, False
            self._working = self._candidate
        else:
            if not _authorized(authorize):
                raise CoachError('reference_forbidden')
            entry = _prepare(value)
            if (entry is None or not self._cohort
                    or not _compatible(self._cohort[-1].row, entry.row)):
                raise CoachError('reference_incompatible')
            self._working, self._grant = entry, authorize
            self._selected, self._focus = True, False
        self.epoch = uuid.uuid4().hex
        return self.snapshot()

    def reference(self):
        self._check_grant()
        return copy.deepcopy(self._working.row) if self.active and self._working else None

    def baseline_laps(self):
        return [copy.deepcopy(e.row) for e in self._cohort[:2]] if self.active else []

    def snapshot(self):
        self._check_grant()
        unknown = [k for k in ('car_id', 'track_id', 'layout', 'compound', 'stint_id',
                               'strategy', 'definition_fingerprint') if not (self._context or {}).get(k)]
        if not (self._context or {}).get('settings_confirmed'):
            unknown.append('settings')
        ref = dict(self._working.public(), source='selected' if self._selected else 'personal') if self._working else None
        retained = {id(e) for e in self._cohort + [e for e in (self._working, self._candidate) if e]}
        return dict(active=self.active, phase=self._phase, accepted=self._accepted,
                    reason=self._reason, training_id=self.training_id, epoch=self.epoch,
                    owner=copy.deepcopy(self.owner), mode=self.mode, objective=self.objective,
                    calibration_ids=[e.row['id'] for e in self._cohort],
                    baseline=copy.deepcopy(self._baseline), reference=ref,
                    focus_active=self._focus, unknown=sorted(unknown),
                    retained_laps=len(retained), dedup_entries=len(self._seen),
                    retired_sessions=len(self._retired),
                    invalidations=copy.deepcopy(list(self._events)))

    def stop(self):
        self.active, self._phase, self._focus = False, 'stopped', False
        self._accepted = False
        self.epoch = uuid.uuid4().hex
        result = self.snapshot()
        self._cohort.clear()
        self._working = self._candidate = None
        self._grant, self._selected = None, False
        return result

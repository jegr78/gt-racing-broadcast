"""Relay-owned live practice actor; controls never wait for lap calculation."""

import collections
import copy
import json
import threading
import time
import uuid

import driver_coach_assessment as assessment
import driver_coach_calibration as calibration
import driver_coach_focus as focus

COMMANDS = frozenset(('start', 'stop', 'objective', 'skill', 'skip', 'keep', 'reference'))
ROWS_PER_STEP = 4
COHORT_MAX = 8


def _allowed(check):
    try:
        return callable(check) and check() is True
    except Exception:
        return False


def _live(doc):
    return (isinstance(doc, dict) and doc.get('receipt', {}).get('state') == 'live'
            and not doc.get('replay', {}).get('active') and calibration._text(doc.get('session_id'))
            and isinstance(doc.get('current'), dict))


def _stamp(doc):
    current = doc.get('current') or {}
    return (doc.get('generation'), doc.get('session_id'), json.dumps(
        {k: current.get(k) for k in calibration.CONTEXT if k != 'session_id'}, sort_keys=True, allow_nan=False))


def _facts(row):
    keys = calibration.CONTEXT + ('time_s', 'state', 'time_valid', 'comparison_eligible',
            'capture_complete', 'trace_complete', 'data_quality', 'analysis_role', 'after_service')
    return (row.get('measurement_hash'), json.dumps({k: row.get(k) for k in keys}, sort_keys=True, allow_nan=False))


def _low(doc):
    values = doc.get('instruments') or {}
    if not assessment.finite(values.get('speed_kmh')):
        return False
    return (0 <= values['speed_kmh'] < 3 or assessment.finite(values.get('brake'))
            and assessment.finite(values.get('steer_rad')) and 0 <= values['brake'] < 5
            and abs(values['steer_rad']) < .1)


def _mark(doc, wall):
    current = doc.get('current') or {}
    return dict(generation=doc.get('generation'), session_id=doc.get('session_id'),
                lap=current.get('lap'), wall=wall)


class Service:
    def __init__(self, profile, source, detail, radio, clock=time.monotonic, wall=time.time, assessor=None):
        self.profile, self.source, self.detail, self.radio = profile, source, detail, radio
        self.clock, self.wall, self.assessor = clock, wall, assessor or assessment.assess
        self._lock = threading.RLock()
        self._work_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._owner = None
        self._grant = None
        self._command = None
        self._model = None
        self._token = uuid.uuid4().hex
        self._revision = 0
        self._lowload_at = None
        self._ready_at = 0
        self._state = dict(active=False, pending=False, phase='inactive', calibration=None, focus=None, latest=None)

    def start(self):
        with self._lock:
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._run, name='driver-coach', daemon=True)
                self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except (OSError, ValueError, TypeError, KeyError):
                with self._lock:
                    self._invalidate('unavailable')
                self.radio.cancel_coach()
            self._stop.wait(.25)

    def _invalidate(self, phase):
        self._token = uuid.uuid4().hex
        self._state.update(phase=phase, latest=None)
        self._lowload_at = None
        self._revision += 1

    def snapshot(self, owner):
        with self._lock:
            if self._owner is not None and owner != self._owner:
                return dict(version=1, revision=self._revision, active=False, pending=False,
                            phase='busy' if self._state['active'] else 'inactive')
            return copy.deepcopy(dict(self._state, version=1, revision=self._revision, epoch=self._token))

    def command(self, owner, action, expected, options=None, authorize=None):
        if (not isinstance(owner, dict) or set(owner) != {'profile', 'subject'}
                or owner.get('profile') != self.profile or not calibration._text(owner.get('subject'))
                or not (owner['subject'] == 'local:producer' or owner['subject'].startswith('console:'))):
            raise calibration.CoachError('invalid_owner')
        if not _allowed(authorize):
            raise calibration.CoachError('forbidden')
        if options is not None and not isinstance(options, dict):
            raise calibration.CoachError('invalid_body')
        options = options or {}
        if not isinstance(action, str) or action not in COMMANDS:
            raise calibration.CoachError('invalid_control')
        allowed = {'start': {'mode', 'objective', 'allow_ai'}, 'objective': {'objective'},
                   'skill': {'skill'}, 'reference': {'row', 'authorize'}}.get(action, set())
        if set(options)-allowed:
            raise calibration.CoachError('invalid_body')
        if (action == 'start' and (options.get('mode', 'practice') not in calibration.MODES
                                  or type(options.get('allow_ai', False)) is not bool)
                or action in ('start', 'objective') and options.get('objective', 'balanced') not in calibration.OBJECTIVES
                or action == 'skill' and options.get('skill') is not None
                and (not isinstance(options['skill'], str) or options['skill'] not in focus.SKILLS)):
            raise calibration.CoachError('invalid_body')
        doc = self.source() if action in ('start', 'objective', 'reference') else None
        with self._lock:
            if self._stop.is_set():
                raise calibration.CoachError('service_closed')
            if type(expected) is not int or expected != self._revision:
                raise calibration.CoachError('conflict')
            if self._state['active'] and owner != self._owner:
                raise calibration.CoachError('training_busy')
            if action != 'start' and (not self._state['active'] or owner != self._owner):
                raise calibration.CoachError('training_required')
            if self._command is not None and action != 'stop':
                raise calibration.CoachError('busy')
            if action in ('skip', 'keep') and not (self._state.get('focus') or {}).get('focus'):
                raise calibration.CoachError('focus_unavailable')
            self._owner, self._grant = copy.deepcopy(owner), authorize
            self._invalidate('pending' if action != 'stop' else 'stopped')
            self._command = dict(action=action, options=copy.deepcopy(options), owner=copy.deepcopy(owner),
                                 token=self._token, doc=doc, wall=self.wall())
            self._state.update(active=action != 'stop', pending=True)
            if action in ('start', 'stop'):
                self._state.update(calibration=None, focus=None, latest=None)
        self.radio.cancel_coach()
        return self.snapshot(owner)

    def _apply(self, command):
        action, options = command['action'], command['options']
        if action == 'stop':
            if self._model:
                self._model['cal'].stop()
            self._model = None
            return
        if action == 'start':
            cal = calibration.Calibration()
            cal.start(command['owner'], mode=options.get('mode', 'practice'), objective=options.get('objective', 'balanced'))
            doc = command['doc']
            self._model = dict(cal=cal, focus=focus.Focus(), cohort=collections.deque(maxlen=COHORT_MAX),
                               stamp=_stamp(doc) if _live(doc) else None,
                               mark=_mark(doc, command['wall']) if _live(doc) else None,
                               seen=collections.OrderedDict(), allow_ai=options.get('allow_ai', False),
                               latest=None, owner=command['owner'], cal_epoch=cal.epoch, reference_grant=None)
            return
        model = self._model
        if model is None:
            return
        if action == 'objective':
            model['cal'].set_objective(options['objective'])
        elif action == 'reference':
            archive = options.get('authorize')
            anchor = _stamp(command['doc']) if _live(command['doc']) else None
            def grant():
                live = self.source()
                return anchor is not None and _live(live) and _stamp(live) == anchor and _allowed(archive)
            model['reference_grant'] = grant if options.get('row') is not None else None
            model['cal'].select_reference(options.get('row'), authorize=grant)
        else:
            model['focus'].control(action, model['focus'].snapshot()['epoch'], skill=options.get('skill'))
        if action in ('objective', 'reference'):
            model['cohort'].clear()
            model['seen'].clear()
            model['mark'] = _mark(command['doc'], command['wall']) if _live(command['doc']) else None
            model['focus'].observe(None, None, model['cal'].snapshot())
        model['latest'] = None

    def _current(self, token, stamp=None):
        with self._lock:
            valid = (not self._stop.is_set() and self._state['active'] and self._token == token
                     and self._command is None)
            grant = self._grant
        if not valid or not _allowed(grant) or self._model and self._model.get('reference_grant') is not None and not _allowed(self._model['reference_grant']):
            return False
        doc = self.source()
        return _live(doc) and (stamp is None or _stamp(doc) == stamp)

    def _speak(self, token):
        doc = self.source()
        if not _live(doc) or not _low(doc):
            return False
        with self._lock:
            return (self._token == token and self._state['active'] and self._command is None
                    and self._lowload_at is not None and self.clock()-self._lowload_at >= 1
                    and self.clock() >= self._ready_at)

    def _opportunity(self, doc):
        low = _low(doc)
        with self._lock:
            if not low:
                self._lowload_at = None
            elif self._lowload_at is None:
                self._lowload_at = self.clock()

    def step(self):
        with self._work_lock:
            with self._lock:
                command, self._command = self._command, None
                token = self._token
            if self._stop.is_set():
                return
            if command:
                self._apply(command)
            model = self._model
            if model is None:
                with self._lock:
                    if token == self._token:
                        self._state.update(pending=False, phase='stopped' if command else 'inactive')
                return
            model['cal'].reference()
            if model['cal'].epoch != model['cal_epoch']:
                model['cal_epoch'] = model['cal'].epoch
                model['focus'].observe(None, None, model['cal'].snapshot())
                model['cohort'].clear(); model['latest'] = None
                model['reference_grant'] = None if model['cal'].snapshot()['reference'] is None or model['cal'].snapshot()['reference']['source'] != 'selected' else model['reference_grant']
                self.radio.cancel_coach()
            doc = self.source()
            if not self._current(token):
                with self._lock:
                    if token == self._token:
                        self._state.update(pending=False, phase='paused', latest=None)
                        self._lowload_at = None
                self.radio.cancel_coach()
                return
            self._opportunity(doc)
            stamp = _stamp(doc)
            if model['stamp'] != stamp:
                model['cal'].invalidate('live_context_changed')
                model['focus'].observe(None, None, model['cal'].snapshot())
                model['stamp'], model['mark'] = stamp, _mark(doc, self.wall())
                model['cohort'].clear(); model['seen'].clear(); model['latest'] = None
                self.radio.cancel_coach()
            mark = model['mark']
            rows = doc.get('laps') or []
            candidates = [r for r in rows[-512:] if isinstance(r, dict) and r.get('session_id') == doc['session_id']
                          and type(r.get('lap')) is int and type(mark.get('lap')) is int
                          and r['lap'] > mark['lap'] and assessment.finite(r.get('_start_wall'))
                          and r['_start_wall'] > mark['wall']]
            processed = 0
            for brief in sorted(candidates, key=lambda r:r['lap']):
                ident = brief.get('id')
                if not calibration._text(ident):
                    continue
                digest = _facts(brief)
                if model['seen'].get(ident) == digest:
                    continue
                if processed >= ROWS_PER_STEP:
                    break
                processed += 1
                detail = self.detail(ident)
                row = detail.get('lap') if isinstance(detail, dict) and detail.get('state') == 'ready' else None
                if (not isinstance(row, dict) or row.get('id') != ident
                        or row.get('session_id') != doc['session_id']
                        or any(row.get(k) != brief.get(k) for k in ('measurement_hash', 'version', 'context_revision'))):
                    continue
                if model['focus'].snapshot()['phase'] == 'completed':
                    model['focus'].finish(); model['cal'].end_focus()
                calibrated = model['cal'].ingest(row)
                frozen = model['cal'].reference()
                calibrated = model['cal'].snapshot()
                if model['cal'].epoch != model['cal_epoch']:
                    model['cal_epoch'] = model['cal'].epoch
                    model['cohort'].clear()
                    self.radio.cancel_coach()
                result = self.assessor(row, frozen, definition=row.get('track_definition'), personal_laps=list(model['cohort']))
                trained = model['focus'].observe(row, result, calibrated, model['cal'].baseline_laps())
                if trained['focus'] and not calibrated['focus_active']:
                    model['cal'].begin_focus()
                if calibrated['accepted']:
                    prepared = calibration._prepare(row)
                    if prepared:
                        model['cohort'].append(prepared.row)
                if not self._current(token, stamp):
                    return
                current = self.source()
                valid = next((r for r in current.get('laps', []) if r.get('id') == ident), None)
                if valid is None or any(valid.get(k) != brief.get(k) for k in ('measurement_hash', 'version', 'context_revision')):
                    return
                model['seen'][ident] = digest
                while len(model['seen']) > 128:
                    model['seen'].popitem(last=False)
                text = ('This lap is unsuitable for a new technique score. Keep the current exercise.'
                        if result['status'] == 'unsuitable' else trained['review'])
                model['latest'] = dict(lap_id=ident, text=text, summary=result['summary'])
                evidence = dict(coach=dict(lap_time_s=row.get('time_s'), baseline_time_s=(calibrated.get('baseline') or {}).get('time_s')),
                                uncertainties=result['unknown'])
                self._ready_at = self.clock()+1.5
                self.radio.publish_coach(text, evidence, model['owner'], token,
                    authorize=lambda token=token,stamp=stamp:self._current(token, stamp),
                    ready=lambda token=token:self._speak(token), allow_ai=model['allow_ai'])
            with self._lock:
                if token == self._token and self._command is None:
                    new = dict(active=True, pending=False, phase=model['cal'].snapshot()['phase'],
                               calibration=model['cal'].snapshot(), focus=model['focus'].snapshot(), latest=model['latest'])
                    if new != self._state:
                        self._state = copy.deepcopy(new); self._revision += 1

    def close(self):
        self._stop.set()
        with self._lock:
            self._invalidate('stopped')
            self._state['active'] = False
        self.radio.cancel_coach()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2)

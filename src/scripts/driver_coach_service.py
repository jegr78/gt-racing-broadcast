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
    if not isinstance(doc, dict):
        return False
    receipt, replay = doc.get('receipt'), doc.get('replay')
    return (isinstance(receipt, dict) and receipt.get('state') == 'live'
            and isinstance(replay, dict) and replay.get('active') is False
            and calibration._text(doc.get('session_id')) and isinstance(doc.get('current'), dict))


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


def _review(row, result, trained):
    if result['status'] == 'unsuitable':
        return dict(lap_id=row['id'], text='This lap is unsuitable for a new technique score. Keep the current exercise.',
                    summary='No new technique score.', observation='This lap is unsuitable for comparison.',
                    interpretation='Current evidence is unavailable.', action=None,
                    check='Wait for a suitable comparable lap.')
    observation = f"Completed lap in {row['time_s']:g} seconds."
    exercise = trained.get('focus')
    interpretation = trained['progress']['text'] if exercise else 'More suitable calibration laps are needed.'
    action = exercise['action'] if exercise else 'Consider repeating a comparable approach for calibration.'
    check = ('Check repeated passage time, its following section and exit speed.' if exercise and exercise['section_id'] is not None
             else 'Check timing variation over repeated comparable laps.')
    label = exercise['label']+'. ' if exercise else ''
    return dict(lap_id=row['id'], observation=observation, interpretation=interpretation,
                action=action, check=check, summary=observation+' '+interpretation,
                text=observation+' '+label+action+' '+check)


def attach(radio, live):
    existing = getattr(radio, 'coach', None)
    if existing is not None:
        return existing
    if (live is None or not radio.settings.available
            or not callable(getattr(live, 'snapshot', None)) or not callable(getattr(live, 'detail', None))):
        return None
    coordinator = Service(radio.settings.path.parent.name, live.snapshot, live.detail, radio)
    radio.coach = coordinator
    return coordinator


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
        self._generation = uuid.uuid4().hex
        self._revision = 0
        self._lowload_at = None
        self._ready_at = 0
        self._checks = (None, ())
        self._state = dict(active=False, pending=False, phase='inactive', calibration=None, focus=None, latest=None, allow_ai=False)

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
                return dict(version=1, generation=self._generation, revision=self._revision, active=False, pending=False,
                            phase='busy' if self._state['active'] else 'inactive')
            return copy.deepcopy(dict(self._state, version=1, generation=self._generation, revision=self._revision, epoch=self._token))

    def view(self, owner):
        for _ in range(2):
            with self._lock:
                state = self.snapshot(owner)
                stamp, expected = self._checks
            if not state['active'] or state['pending']:
                return state
            current = self._current(state['epoch'], stamp, expected)
            with self._lock:
                if state['revision'] != self._revision or state['epoch'] != self._token:
                    continue
            if not current:
                state.update(phase='paused', calibration=None, focus=None, latest=None)
            return state
        state = self.snapshot(owner)
        if state['active']:
            state.update(phase='paused', calibration=None, focus=None, latest=None)
        return state

    def command(self, owner, action, expected, options=None, authorize=None, training_authorize=None):
        if (not calibration.valid_owner(owner) or owner.get('profile') != self.profile
                or not (owner['subject'] == 'local:producer' or owner['subject'].startswith('console:'))):
            raise calibration.CoachError('invalid_owner')
        if not _allowed(authorize) or training_authorize is not None and not _allowed(training_authorize):
            raise calibration.CoachError('forbidden')
        if options is not None and not isinstance(options, dict):
            raise calibration.CoachError('invalid_body')
        options = options or {}
        if not isinstance(action, str) or action not in COMMANDS:
            raise calibration.CoachError('invalid_control')
        allowed = {'start': {'mode', 'objective', 'allow_ai'}, 'objective': {'objective'},
                   'skill': {'skill'}, 'reference': {'row', 'authorize'}}.get(action, set())
        if set(options)-allowed or action == 'objective' and 'objective' not in options or action == 'reference' and 'row' not in options:
            raise calibration.CoachError('invalid_body')
        if (action == 'start' and (options.get('mode', 'practice') not in calibration.MODES
                                  or type(options.get('allow_ai', False)) is not bool)
                or action in ('start', 'objective') and options.get('objective', 'balanced') not in calibration.OBJECTIVES
                or action == 'skill' and options.get('skill') is not None
                and (not isinstance(options['skill'], str) or options['skill'] not in focus.SKILLS)):
            raise calibration.CoachError('invalid_body')
        doc = self.source() if action in ('start', 'objective', 'reference') else None
        if not _allowed(authorize) or training_authorize is not None and not _allowed(training_authorize):
            raise calibration.CoachError('forbidden')
        with self._lock:
            if self._stop.is_set():
                raise calibration.CoachError('service_closed')
            if type(expected) is not int or expected < 0 or action != 'stop' and expected != self._revision:
                raise calibration.CoachError('conflict')
            if self._state['active'] and owner != self._owner:
                raise calibration.CoachError('training_busy')
            if action != 'start' and (not self._state['active'] or owner != self._owner):
                raise calibration.CoachError('training_required')
            if (self._command is not None or self._state['pending']) and action != 'stop':
                raise calibration.CoachError('busy')
            if action in ('skip', 'keep') and not (self._state.get('focus') or {}).get('focus'):
                raise calibration.CoachError('focus_unavailable')
            self._owner = copy.deepcopy(owner)
            self._grant = authorize if training_authorize is None else training_authorize
            self._invalidate('pending' if action != 'stop' else 'stopped')
            self._command = dict(action=action, options=copy.deepcopy(options), owner=copy.deepcopy(owner),
                                 token=self._token, doc=doc, wall=self.wall(), authorize=authorize)
            self._state.update(active=action != 'stop', pending=True)
            if action in ('start', 'stop'):
                self._state.update(calibration=None, focus=None, latest=None,
                                   allow_ai=options.get('allow_ai', False) if action == 'start' else False)
        self.radio.cancel_coach(blocking=False)
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
                               latest=None, owner=command['owner'], cal_epoch=cal.epoch, reference_grant=None,
                               sources=collections.OrderedDict(), best_source=None, focus_key=None, focus_sources=set(), cursor=0, detail_cursor=0, detail_status=None)
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
            model['cal'].select_reference(options.get('row'), authorize=grant)
            model['reference_grant'] = grant if options.get('row') is not None else None
        elif action == 'skill':
            model['focus'].set_skill(options.get('skill'))
        else:
            model['focus'].control(action, model['focus'].snapshot()['epoch'])
        if action in ('objective', 'reference'):
            model['cohort'].clear()
            model['seen'].clear()
            model['mark'] = _mark(command['doc'], command['wall']) if _live(command['doc']) else None
            model['focus'].observe(None, None, model['cal'].snapshot())
        model['latest'] = None

    def _current(self, token, stamp=None, expected=()):
        with self._lock:
            valid = (not self._stop.is_set() and self._state['active'] and self._token == token
                     and self._command is None and self._model is not None and self._model['owner'] == self._owner)
            grant = self._grant
        if not valid or not _allowed(grant) or self._model and self._model.get('reference_grant') is not None and not _allowed(self._model['reference_grant']):
            return False
        doc = self.source()
        if not _live(doc) or stamp is not None and _stamp(doc) != stamp:
            return False
        lookup = {row.get('id'): row for row in (doc.get('laps') or [])[-512:] if isinstance(row, dict)}
        consistent = all(ident in lookup and _facts(lookup[ident]) == facts for ident, facts in expected)
        with self._lock:
            return (consistent and not self._stop.is_set() and self._token == token
                    and self._state['active'] and self._command is None)

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

    def _sync_epoch(self, model, calibrated):
        if calibrated['epoch'] == model['cal_epoch']:
            return False
        model['cal_epoch'] = calibrated['epoch']
        model['focus'].observe(None, None, calibrated)
        model['cohort'].clear(); model['latest'] = None
        model['focus_key'], model['focus_sources'] = None, set()
        if calibrated['baseline'] is None:
            model['sources'].clear(); model['best_source'] = None
        if (calibrated.get('reference') or {}).get('source') != 'selected':
            model['reference_grant'] = None
        self.radio.cancel_coach()
        return True

    def _reset_evidence(self, model, doc, reason):
        model['cal'].invalidate(reason)
        self._sync_epoch(model, model['cal'].snapshot())
        model['stamp'], model['mark'] = _stamp(doc), _mark(doc, self.wall())
        model['seen'].clear(); model['sources'].clear(); model['best_source'] = None
        model['cursor'] = 0
        model['detail_cursor'], model['detail_status'] = 0, None

    def _cache(self, token, model, calibrated=None):
        # Grant callbacks may perform bounded I/O; never run them under the state lock.
        calibrated = model['cal'].snapshot() if calibrated is None else calibrated
        self._sync_epoch(model, calibrated)
        new = dict(active=calibrated['active'], pending=False, phase=calibrated['phase'],
                   calibration=calibrated, focus=model['focus'].snapshot(), latest=model['latest'],
                   allow_ai=model['allow_ai'] if calibrated['active'] else False, detail_status=model['detail_status'])
        with self._lock:
            if token == self._token and self._command is None:
                self._checks = (model['stamp'], tuple(model['sources'].items()))
                if new != self._state:
                    self._state = copy.deepcopy(new); self._revision += 1

    def step(self):
        with self._work_lock:
            with self._lock:
                command, self._command = self._command, None
                token = self._token
            if self._stop.is_set():
                return
            if command:
                if command['action'] != 'stop' and not _allowed(command['authorize']):
                    with self._lock:
                        if token == self._token:
                            self._model = None
                            self._state.update(active=False, pending=False, phase='forbidden', calibration=None, focus=None, latest=None)
                            self._revision += 1
                    self.radio.cancel_coach()
                    return
                self._apply(command)
            model = self._model
            if model is not None and not _allowed(self._grant):
                with self._lock:
                    if token == self._token:
                        self._model = None
                        self._invalidate('forbidden')
                        self._state.update(active=False, pending=False, calibration=None, focus=None, latest=None)
                self.radio.cancel_coach()
                return
            if model is None:
                with self._lock:
                    if token == self._token:
                        new_phase = 'stopped' if command else 'inactive'
                        if self._state['pending'] or self._state['phase'] != new_phase:
                            self._state.update(active=False, pending=False, phase=new_phase)
                            self._revision += 1
                return
            model['cal'].reference()
            calibrated = model['cal'].snapshot()
            self._sync_epoch(model, calibrated)
            if not calibrated['active']:
                self._cache(token, model, calibrated)
                return
            doc = self.source()
            if not self._current(token):
                with self._lock:
                    if token == self._token:
                        if self._state['pending'] or self._state['phase'] != 'paused' or self._state['latest'] is not None:
                            self._state.update(pending=False, phase='paused', latest=None)
                            self._revision += 1
                        self._lowload_at = None
                self.radio.cancel_coach()
                return
            self._opportunity(doc)
            stamp = _stamp(doc)
            if model['stamp'] != stamp:
                self._reset_evidence(model, doc, 'live_context_changed')
            lookup = {r.get('id'): r for r in (doc.get('laps') or [])[-512:] if isinstance(r, dict)}
            if any(ident in lookup and _facts(lookup[ident]) != facts for ident, facts in model['sources'].items()):
                self._reset_evidence(model, doc, 'source_evidence_corrected')
            mark = model['mark']
            rows = doc.get('laps') or []
            candidates = [r for r in rows[-512:] if isinstance(r, dict) and r.get('state') == 'confirmed'
                          and r.get('session_id') == doc['session_id']
                          and type(r.get('lap')) is int and type(mark.get('lap')) is int
                          and r['lap'] > mark['lap'] and assessment.finite(r.get('_start_wall'))
                          and r['_start_wall'] > mark['wall']]
            # Rotate bounded detail attempts so pending/evicted rows cannot starve ready data.
            ordered = sorted((r for r in candidates if r['lap'] > model['cursor']), key=lambda r:r['lap'])
            remaining = [r for r in ordered if r['lap'] > model['detail_cursor']]
            if not remaining:
                remaining = ordered
            processed = 0
            for brief in remaining:
                ident = brief.get('id')
                if not calibration._text(ident):
                    continue
                digest = _facts(brief)
                if brief['lap'] <= model['cursor'] or model['seen'].get(ident) == digest:
                    continue
                if processed >= ROWS_PER_STEP:
                    break
                processed += 1
                model['detail_cursor'] = brief['lap']
                detail = self.detail(ident)
                detail_state = detail.get('state') if isinstance(detail, dict) else 'unavailable'
                if detail_state != 'ready':
                    model['detail_status'] = dict(lap_id=ident, state=detail_state if detail_state in ('evicted', 'unavailable', 'pending') else 'unavailable')
                    if detail_state in ('evicted', 'unavailable'):
                        model['cursor'] = brief['lap']
                    continue
                row = detail.get('lap') if isinstance(detail, dict) and detail.get('state') == 'ready' else None
                if (not isinstance(row, dict) or row.get('id') != ident
                        or row.get('session_id') != doc['session_id']
                        or any(row.get(k) != brief.get(k) for k in ('measurement_hash', 'version', 'context_revision', 'lap', 'time_s', '_start_wall', '_end_wall', 'state'))):
                    continue
                if model['focus'].snapshot()['phase'] == 'completed':
                    model['focus'].finish(); model['cal'].end_focus()
                model['cal'].ingest(row)
                frozen = model['cal'].reference()
                calibrated = model['cal'].snapshot()
                if self._sync_epoch(model, calibrated):
                    frozen = model['cal'].reference()
                    calibrated = model['cal'].snapshot()
                if not calibrated['active']:
                    self._cache(token, model, calibrated)
                    return
                result = self.assessor(row, frozen, definition=row.get('track_definition'), personal_laps=list(model['cohort']))
                trained = model['focus'].observe(row, result, calibrated, model['cal'].baseline_laps())
                focus_key = (trained['epoch'], trained['focus']['id']) if trained['focus'] else None
                if focus_key != model['focus_key']:
                    model['focus_key'] = focus_key
                    model['focus_sources'] = {r['id'] for r in model['cohort']} | {ident} if focus_key else set()
                if trained['focus'] and not calibrated['focus_active']:
                    model['cal'].begin_focus()
                if calibrated['accepted']:
                    prepared = calibration._prepare(row)
                    if prepared:
                        model['cohort'].append(prepared.row)
                        if model['best_source'] is None or row['time_s'] < model['best_source'][0]:
                            model['best_source'] = (row['time_s'], ident)
                if not self._current(token, stamp):
                    return
                current = self.source()
                valid = next((r for r in current.get('laps', []) if r.get('id') == ident), None)
                if valid is None or any(valid.get(k) != brief.get(k) for k in ('measurement_hash', 'version', 'context_revision')):
                    return
                model['sources'][ident] = digest
                required = set((calibrated.get('baseline') or {}).get('lap_ids', [])) | {ident}
                reference = calibrated.get('reference') or {}
                if reference.get('source') == 'personal':required.add(reference['id'])
                required.update(model['focus_sources'])
                required.update(r['id'] for r in model['cohort'])
                required.update(r['lap_id'] for r in trained['attempts'][-3:])
                pins = required | ({model['best_source'][1]} if model['best_source'] else set())
                while len(model['sources']) > 128:
                    disposable = next(key for key in model['sources'] if key not in pins)
                    del model['sources'][disposable]
                expected = tuple((key, model['sources'].get(key)) for key in sorted(required))
                model['cursor'] = brief['lap']
                model['seen'][ident] = digest
                while len(model['seen']) > 128:
                    model['seen'].popitem(last=False)
                model['latest'] = _review(row, result, trained)
                text = model['latest']['text']
                exercise = trained.get('focus')
                recent = trained['attempts'][-1] if trained['attempts'] and trained['attempts'][-1]['lap_id'] == ident else {}
                measured_check = recent.get('check') or {}
                original = trained.get('baseline') or {}
                passage = original.get('passage') or {}
                progress = trained['progress']
                context = dict(skill=exercise['skill'] if exercise else 'consistency',
                    label=exercise['label'] if exercise else 'Personal calibration',
                    reviewed=exercise['reviewed'] if exercise else False, objective=calibrated['objective'],
                    observation=model['latest']['observation'], interpretation=model['latest']['interpretation'],
                    check=model['latest']['check'], variants={
                        'current':model['latest']['action'],
                        'repeat':'Consider repeating the current exercise without adding another change.' if exercise
                                 else 'Consider repeating a comparable approach while collecting calibration evidence.'})
                evidence = dict(coach=dict(lap_time_s=row.get('time_s'),
                    baseline_time_s=(calibrated.get('baseline') or {}).get('time_s'),
                    window_time_s=measured_check.get('own_s'), baseline_window_s=passage.get('time_s'),
                    recent_range_s=progress.get('measure_range_s'), baseline_range_s=passage.get('range_s', original.get('range_s')),
                    sample_count=progress.get('sample_count'), exit_speed_kmh=measured_check.get('exit_speed_kmh'),
                    baseline_exit_speed_kmh=passage.get('exit_speed_kmh')),
                    uncertainties=result['unknown'], coach_context=context)
                completed = brief.get('_end_wall')
                completion = completed if assessment.finite(completed) else float('-inf')
                latest_lap = max((r['lap'] for r in candidates), default=None)
                self._ready_at = self.clock()+max(0, completion+1.5-self.wall())
                self.radio.publish_coach(text, evidence, model['owner'], token,
                    authorize=lambda token=token,stamp=stamp,expected=expected:self._current(token, stamp, expected),
                    ready=lambda token=token:self._speak(token), allow_ai=model['allow_ai'] and calibrated['accepted'],
                    completed_at=completion, latest=brief['lap'] == latest_lap)
            self._cache(token, model)

    def close(self):
        self._stop.set()
        with self._lock:
            self._invalidate('stopped')
            self._state['active'] = False
        self.radio.cancel_coach(blocking=False)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2)

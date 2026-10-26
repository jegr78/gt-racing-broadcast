"""One retained practice focus with bounded, comparable repeated-attempt progress."""

import collections
import copy
import math

import driver_coach_calibration as calibration
import driver_coach_assessment as measured

ATTEMPTS_MAX = 12
SEEN_MAX = 128
HISTORY_MAX = 16
FINDINGS_MAX = 24
DISMISSED_MAX = 128
SKILLS = frozenset(('braking', 'throttle', 'steering', 'gear', 'speed', 'line', 'pace', 'consistency'))


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value, maximum=160):
    return isinstance(value, str) and 0 < len(value) <= maximum and all(ord(c) >= 32 for c in value)


def _check(value):
    fields = ('start_m', 'corner_exit_m', 'end_m', 'own_s', 'reference_s', 'net_delta_s', 'corner_delta_s',
              'own_downstream_s', 'reference_downstream_s', 'downstream_delta_s',
              'exit_speed_kmh', 'reference_exit_speed_kmh', 'exit_speed_delta_kmh', 'screen_s')
    if (not isinstance(value, dict) or value.get('available') is not True
            or not all(_number(value.get(k)) for k in fields)):
        return None
    result = {k: value[k] for k in fields}
    if (not 0 <= result['start_m'] < result['corner_exit_m'] < result['end_m'] <= 100000
            or min(result['own_s'], result['reference_s'], result['screen_s'],
                   result['own_downstream_s'], result['reference_downstream_s']) <= 0
            or min(result['exit_speed_kmh'], result['reference_exit_speed_kmh']) < 0):
        return None
    net = result['own_s'] - result['reference_s']
    downstream = net - result['corner_delta_s']
    if (not _number(downstream) or abs(net - result['net_delta_s']) > .001
            or abs(downstream - result['downstream_delta_s']) > .001
            or abs(result['own_downstream_s']-result['reference_downstream_s']-downstream) > .001
            or abs(result['exit_speed_kmh']-result['reference_exit_speed_kmh']-result['exit_speed_delta_kmh']) > .001
            or result['own_downstream_s'] >= result['own_s']
            or result['reference_downstream_s'] >= result['reference_s']):
        return None
    # A supplied result label and an artificially tiny screen never establish gain.
    result['screen_s'] = max(.05, result['reference_s'] * .005, result['screen_s'])
    result['available'] = True
    return result


def _finding(row, lap_id):
    if (not isinstance(row, dict) or not isinstance(row.get('skill'), str) or row['skill'] not in SKILLS
            or not _text(row.get('id'), 256)
            or row.get('section_id') is not None and not _text(row['section_id'], 80)
            or type(row.get('support')) is not int or not 0 <= row['support'] <= 8
            or not _number(row.get('strength')) or not 0 <= row['strength'] <= 3
            or not all(_text(row.get(k), 600) for k in ('observation', 'interpretation', 'action'))):
        return None
    evidence = row.get('evidence')
    if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 8
            or not all(isinstance(e, dict) and _text(e.get('id'), 512)
                       and _text(e.get('lap_id')) and _text(e.get('unit'), 32)
                       and _number(e.get('value')) for e in evidence)
            or not any(e['lap_id'] == lap_id for e in evidence)):
        return None
    result = {k: copy.deepcopy(row[k]) for k in ('id', 'section_id', 'skill', 'support', 'strength',
                                               'observation', 'interpretation', 'action')}
    result.update(label=row.get('label') if _text(row.get('label'), 200) else 'Practice focus',
                  reviewed=row.get('reviewed') is True,
                  evidence=[{k: e[k] for k in ('id', 'lap_id', 'value', 'unit')} for e in evidence],
                  check=_check(row.get('check')))
    if result['section_id'] is not None and result['check'] is None:
        return None
    return result


def _lap(value):
    if not isinstance(value, dict):
        return None
    fields = set(calibration.CONTEXT) | {
        'id', 'lap', 'time_s', 'state', 'time_valid', 'comparison_eligible',
        'capture_complete', 'trace_complete', 'data_quality', 'analysis_role', 'after_service'}
    prepared = calibration._prepare({k: v for k, v in value.items() if k in fields})
    return prepared.row if prepared else None


def _passage(rows, check):
    measurements = []
    for row in rows:
        trace = measured._trace(row)
        if not trace:
            return None
        own = measured._duration(trace, check['start_m'], check['end_m'])
        downstream = measured._duration(trace, check['corner_exit_m'], check['end_m'])
        exit_point = next((p for p in trace if p['d'] >= check['corner_exit_m']), None)
        speed = exit_point.get('speed_kmh') if exit_point else None
        if own is None or downstream is None or not _number(speed) or speed < 0:
            return None
        measurements.append(dict(time_s=own, downstream_s=downstream, exit_speed_kmh=speed))
    result = {k: min(m[k] for m in measurements)+(max(m[k] for m in measurements)-min(m[k] for m in measurements))/2
              for k in ('time_s', 'downstream_s', 'exit_speed_kmh')}
    times = [m['time_s'] for m in measurements]
    result.update(start_m=check['start_m'], corner_exit_m=check['corner_exit_m'], end_m=check['end_m'],
                  range_s=max(times)-min(times), lap_times_s=times)
    return result


def _general():
    return dict(id='lap/consistency', section_id=None, skill='consistency', support=0, strength=0,
                label='Personal lap consistency', reviewed=False, evidence=[], check=None,
                observation='Suitable personal laps provide measured timing variation.',
                interpretation='A repeated result describes personal repeatability; its cause remains unknown.',
                action='Consider repeating one comparable approach over several suitable laps.')


class Focus:
    """Caller serializes operations and supplies current authorized calibration/evidence."""

    def __init__(self):
        self._owner = None
        self._binding = None
        self._epoch = None
        self._history = collections.deque(maxlen=HISTORY_MAX)
        self._skill = None
        self._reset()

    def _reset(self):
        self._focus = None
        self._baseline = None
        self._context = None
        self._reference = None
        self._benchmark = None
        self._phase = 'waiting'
        self._objective = 'balanced'
        self._attempts = collections.deque(maxlen=ATTEMPTS_MAX)
        self._seen = collections.OrderedDict()
        self._latest = 0
        self._skipped = set()
        self._completed = set()
        self._keep = False
        self._review = 'Complete comparable calibration laps before choosing an exercise.'
        self._progress = dict(result='waiting', sample_count=0, pace_improved=False,
                              consistency_improved=False, text='More comparable evidence is required.')

    def _record(self, state):
        if self._focus is not None:
            self._history.append(dict(state=state, epoch=self._epoch, focus_id=self._focus['id'],
                                      baseline_id=self._baseline['id'] if self._baseline else None,
                                      reference_id=self._reference, baseline=copy.deepcopy(self._baseline),
                                      attempt_lap_ids=[r['lap_id'] for r in list(self._attempts)[-3:]],
                                      progress=copy.deepcopy(self._progress)))

    def _unavailable(self):
        self._record('invalidated')
        self._reset()
        self._binding = self._epoch = None
        return False

    def _sync(self, snapshot):
        if not isinstance(snapshot, dict):
            return self._unavailable()
        owner = snapshot.get('owner')
        if (not calibration.valid_owner(owner)
                or not _text(snapshot.get('epoch')) or not _text(snapshot.get('training_id'))
                or snapshot.get('objective') not in ('balanced', 'consistency', 'pace')):
            return self._unavailable()
        if self._owner is not None and owner != self._owner:
            # An instance belongs to one owner; a caller must create a fresh one.
            self._unavailable()
            self._history.clear()
            return False
        self._owner = copy.deepcopy(owner)
        baseline, reference = snapshot.get('baseline'), snapshot.get('reference')
        key = (snapshot['epoch'], snapshot['training_id'], snapshot['objective'],
               baseline.get('id') if isinstance(baseline, dict) else None,
               reference.get('id') if isinstance(reference, dict) else None)
        if key != self._binding or snapshot.get('active') is not True:
            self._record('invalidated')
            self._reset()
            self._binding, self._epoch = key, snapshot['epoch']
            self._objective = snapshot['objective']
        return (snapshot.get('active') is True and snapshot.get('phase') in ('provisional', 'ready')
                and isinstance(baseline, dict) and _text(baseline.get('id'))
                and isinstance(reference, dict) and _text(reference.get('id')))

    def _select(self, findings, row, snapshot, baseline, assessment):
        rows = [_lap(r) for r in baseline] if isinstance(baseline, (list, tuple)) and len(baseline) == 2 else []
        if (not rows or any(r is None for r in rows)
                or [r.get('id') for r in rows] != snapshot['baseline'].get('lap_ids')
                or len({r.get('id') for r in rows}) != 2
                or any(r.get('session_id') != row['session_id'] or not calibration._compatible(row, r) for r in rows)):
            return False
        times = [r['time_s'] for r in rows]
        low, high = sorted(times)
        self._baseline = dict(id=snapshot['baseline']['id'], lap_ids=[r['id'] for r in rows],
                              time_s=low+(high-low)/2, range_s=high-low)
        self._reference = snapshot['reference']['id']
        self._benchmark = None
        self._context = {k: copy.deepcopy(row.get(k)) for k in calibration.CONTEXT}
        self._context.update(session_id=row['session_id'], comparison_eligible=True)
        choices = [f for f in findings if len(self._skipped | self._completed) < DISMISSED_MAX
                   and f['id'] not in self._skipped | self._completed
                   and (self._skill is None or f['skill'] == self._skill)]
        relevance = {'pace': {'pace': 2}, 'consistency': {'consistency': 2}, 'balanced': {}}[self._objective]
        choices.sort(key=lambda f: (f['support'], relevance.get(f['skill'], 1), f['strength']), reverse=True)
        # Only the original two actual laps define progress, even with a slower selected target.
        self._focus = None
        for choice in choices:
            if choice['section_id'] is not None:
                passage = _passage(baseline, choice['check'])
                reference_time = (choice['check']['reference_s'] if assessment.get('reference_id') == self._reference else
                                  choice['check']['own_s'] if self._reference == row['id'] else None)
                if passage is None or reference_time is None:
                    continue
                self._baseline['passage'] = passage
                self._benchmark = dict(lap_id=self._reference, time_s=reference_time)
            self._focus = copy.deepcopy(choice)
            break
        if self._focus is None:
            if self._skill not in (None, 'consistency'):
                self._review = 'Current evidence does not support an exercise for the selected skill.'
                return False
            self._focus = _general()
        self._keep = False
        self._attempts.clear()
        self._phase = 'provisional' if snapshot['phase'] == 'provisional' else 'active'
        self._progress = dict(result='provisional', sample_count=0, pace_improved=False,
                              consistency_improved=False, text='Repeat the exercise over at least three comparable attempts.')
        return True

    def observe(self, row, assessment, snapshot, baseline_laps=()):
        """One completed-lap review; unsuitable evidence retains the current exercise."""
        ready = self._sync(snapshot)
        row = _lap(row)
        self._review = 'This lap adds no new technique score. Keep the current exercise.'
        if (not ready or not isinstance(row, dict) or not calibration.eligible(row)
                or not _text(row.get('id')) or not _text(row.get('session_id'))
                or type(row.get('lap')) is not int or row['lap'] <= self._latest
                or snapshot.get('accepted') is not True
                or not isinstance(assessment, dict) or assessment.get('lap_id') != row['id']
                or assessment.get('status') not in ('ready', 'insufficient')):
            return self.snapshot()
        key = (row['session_id'], row['id'])
        if key in self._seen:
            return self.snapshot()
        self._latest = row['lap']
        self._seen[key] = None
        while len(self._seen) > SEEN_MAX:
            self._seen.popitem(last=False)
        raw = assessment.get('findings')
        findings = [f for r in raw if (f := _finding(r, row['id'])) is not None] if isinstance(raw, list) and len(raw) <= FINDINGS_MAX else []
        self._review = 'Completed a suitable lap. ' + (self._focus['action'] if self._focus else 'Choose one repeatable exercise.')
        if self._phase == 'completed' and not self._keep:
            self._record('completed')
            if len(self._completed | self._skipped) < DISMISSED_MAX:
                self._completed.add(self._focus['id'])
            else:
                findings = []
            self._focus = None
        if self._focus is None:
            if self._select(findings, row, snapshot, baseline_laps, assessment):
                self._review = 'Completed a suitable lap. ' + self._focus['action']
            return self.snapshot()
        if (row['session_id'] != self._context['session_id']
                or not calibration._compatible(row, self._context)):
            self._progress['result'] = 'evidence_unavailable'
            return self.snapshot()
        seconds = row['time_s']
        check = None
        if self._focus['section_id'] is not None:
            checks = assessment.get('section_checks', [])
            valid = [_check(c.get('check')) for c in checks
                     if isinstance(c, dict) and c.get('section_id') == self._focus['section_id']] if isinstance(checks, list) and len(checks) <= 128 else []
            valid += [f['check'] for f in findings if f['section_id'] == self._focus['section_id'] and f['check']]
            original = self._focus['check']
            check = next((c for c in valid if c and all(c[k] == original[k] for k in ('start_m', 'corner_exit_m', 'end_m'))
                          and c['reference_s'] == self._benchmark['time_s']), None)
            if check is None or assessment.get('reference_id') != self._reference:
                self._progress['result'] = 'evidence_unavailable'
                return self.snapshot()
            passage = self._baseline['passage']
            screen = max(.05, passage['time_s']*.005)
            pace = (check['own_s']-passage['time_s'] < -screen
                    and check['own_downstream_s']-passage['downstream_s'] <= screen
                    and check['exit_speed_kmh']-passage['exit_speed_kmh'] >= -2)
            slower = check['own_s']-passage['time_s'] > screen
        else:
            screen = max(.05, self._baseline['time_s']*.001)
            pace = seconds-self._baseline['time_s'] < -screen
            slower = seconds-self._baseline['time_s'] > screen
        self._attempts.append(dict(lap_id=row['id'], lap=row['lap'], time_s=seconds,
                                   pace_gain=pace, slower=slower,
                                   measure_s=check['own_s'] if check else seconds, check=copy.deepcopy(check)))
        self._evaluate()
        return self.snapshot()

    def _evaluate(self):
        samples = list(self._attempts)[-3:]
        times = [r['measure_s'] for r in samples]
        spread = max(times)-min(times)
        original = self._baseline.get('passage', self._baseline)['range_s']
        repeated = len(samples) == 3
        pace = repeated and all(r['pace_gain'] for r in samples)
        consistent = repeated and original-spread > max(.05, original*.1)
        success = {'pace': pace, 'consistency': consistent, 'balanced': pace and consistent}[self._objective]
        worse = repeated and (all(r['slower'] for r in samples) if self._objective == 'pace'
                              else spread-original > max(.05, original*.1))
        maintained = repeated and original == 0 and spread <= .05
        result = ('repeated_improvement' if success else 'repeated_regression' if worse else
                  'repeatability_maintained' if maintained else 'provisional')
        self._phase = ('retained' if self._keep else 'completed') if success else 'regression' if worse else 'active'
        self._progress = dict(result=result, sample_count=len(samples), pace_improved=pace,
                              consistency_improved=consistent, measure_range_s=spread,
                              measure='section' if self._focus['section_id'] is not None else 'lap',
                              lap_range_s=max(r['time_s'] for r in samples)-min(r['time_s'] for r in samples),
                              baseline_range_s=original,
                              text=('Repeated measured improvement; the cause remains unknown.' if success else
                                    'Repeated measurements are provisional; their cause remains unknown.'))

    def finish(self):
        """Complete this exercise before the coordinator promotes a working reference."""
        if self._phase != 'completed' or self._focus is None:
            raise calibration.CoachError('focus_not_completed')
        self._record('completed')
        if len(self._completed | self._skipped) < DISMISSED_MAX:
            self._completed.add(self._focus['id'])
        self._focus = None
        self._attempts.clear()
        self._phase = 'waiting'
        self._progress = dict(result='waiting', sample_count=0, pace_improved=False,
                              consistency_improved=False, text='A new exercise has not been scored.')
        self._review = 'Choose a new exercise on the next suitable lap.'
        return self.snapshot()

    def set_skill(self, skill):
        """Trusted coordinator preference, also valid before calibration binds."""
        if skill is not None and (not isinstance(skill, str) or skill not in SKILLS):
            raise calibration.CoachError('invalid_skill')
        if self._binding is None:
            self._skill = skill
            return self.snapshot()
        return self.control('skill', self._epoch, skill=skill)

    def control(self, action, epoch, skill=None):
        if epoch != self._epoch or self._binding is None:
            raise calibration.CoachError('conflict')
        if action not in ('skill', 'skip', 'keep'):
            raise calibration.CoachError('invalid_control')
        if action == 'skill' and skill is not None and (not isinstance(skill, str) or skill not in SKILLS):
            raise calibration.CoachError('invalid_skill')
        if action == 'keep':
            if self._focus is None:
                raise calibration.CoachError('focus_unavailable')
            self._keep = True
            if self._phase == 'completed':
                self._phase = 'retained'
        elif action == 'skip' or skill != self._skill:
            if action == 'skip' and self._focus is not None and len(self._skipped | self._completed) >= DISMISSED_MAX:
                raise calibration.CoachError('focus_limit')
            self._record('skipped')
            if action == 'skip' and self._focus is not None:
                self._skipped.add(self._focus['id'])
            if action == 'skill':
                self._skill = skill
            self._focus = None
            self._attempts.clear()
            self._phase = 'waiting'
            self._keep = False
            self._progress = dict(result='waiting', sample_count=0, pace_improved=False,
                                  consistency_improved=False, text='A new exercise has not been scored.')
            self._review = 'Choose a new exercise on the next suitable lap.'
        return self.snapshot()

    def snapshot(self):
        return copy.deepcopy(dict(epoch=self._epoch, objective=self._objective, phase=self._phase,
                                  skill=self._skill, focus=self._focus, baseline=self._baseline,
                                  reference_id=self._reference, reference_benchmark=self._benchmark, attempts=list(self._attempts),
                                  progress=self._progress, review=self._review,
                                  seen_count=len(self._seen), history=list(self._history)))

"""Bounded deterministic driving observations, experiments and downstream checks."""

import bisect
import hashlib
import json
import math

import ai_driver_review as driver
import gt7_context
import gt7_laps
import gt7_track_definitions

TRACE_MAX = 20001
CORNERS_MAX = 128
COHORT_MAX = 8
FINDINGS_MAX = 24
SCREENS = {
    'brake_first_m': ('braking', 'm', 2 * gt7_laps.STEP_M),
    'brake_last_m': ('braking', 'm', 2 * gt7_laps.STEP_M),
    'full_throttle_after_min_m': ('throttle', 'm', 2 * gt7_laps.STEP_M),
    'peak_abs_steering_deg': ('steering', 'deg', 5),
    'exit_gear': ('gear', 'gear', 1),
    'exit_speed_kmh': ('speed', 'km/h', 2),
    'line_separation_m': ('line', 'm', 1),
}
LABELS = {
    'brake_first_m': 'first sampled brake application',
    'brake_last_m': 'last sampled brake application',
    'full_throttle_after_min_m': 'full-throttle station after measured minimum speed',
    'peak_abs_steering_deg': 'peak absolute steering angle',
    'exit_gear': 'gear at the measured exit',
    'exit_speed_kmh': 'speed at the measured exit',
    'line_separation_m': 'sampled separation from the reference line',
    'window_time_s': 'entry-to-following-exit time',
}
ACTIONS = {
    'braking': 'Consider one small, repeatable brake-release experiment while keeping the rest of the approach consistent.',
    'throttle': 'Consider one smooth, repeatable throttle transition while keeping the rest of the approach consistent.',
    'steering': 'Consider repeating a smoother steering input while keeping the rest of the approach consistent.',
    'gear': 'Consider repeating a chosen gear sequence while keeping the rest of the approach consistent.',
    'speed': 'Consider repeating the entry approach and checking the exit before changing another input.',
    'line': 'Consider repeating a chosen line while keeping the pedal approach consistent.',
    'pace': 'Consider repeating the measured approach to check whether the passage result is repeatable.',
    'consistency': 'Consider repeating the same approach over several suitable laps before making another change.',
}


def finite(value):
    try:
        return driver.finite(value)
    except OverflowError:
        return False


def _eligible(row):
    return (isinstance(row, dict) and isinstance(row.get('id'), str)
            and 0 < len(row['id']) <= 160 and _scope(row) is not None
            and (row.get('car_id') is None or type(row['car_id']) is int and row['car_id'] > 0)
            and all(row.get(k) is None or isinstance(row[k], str) and 0 < len(row[k]) <= 160
                    for k in ('track_id', 'layout', 'stint_id', 'definition_fingerprint'))
            and all(isinstance(row.get(k, {}), dict) for k in ('strategy', 'context_settings'))
            and row.get('state') == 'confirmed'
            and row.get('comparison_eligible') is True and row.get('capture_complete') is True
            and row.get('trace_complete') is True and row.get('time_valid') is True
            and row.get('data_quality') == 'ok' and row.get('analysis_role') == 'regular'
            and not row.get('after_service') and finite(row.get('time_s')) and row['time_s'] > 0)


def _scope(row):
    session = row.get('session_id')
    if isinstance(session, str) and 0 < len(session) <= 160:
        return 'live:' + session
    recording = row.get('recording_id', row.get('rec'))
    if session is None and isinstance(recording, str) and 0 < len(recording) <= 160 and type(row.get('session')) is int:
        return ('recording', recording, row['session'])
    return None


def _compatible(a, b):
    if not _eligible(b):
        return False
    if any(a.get(k) != b.get(k) for k in ('car_id', 'track_id', 'layout', 'definition_fingerprint')):
        return False
    if _scope(a) != _scope(b) and not (a.get('car_id') and a.get('track_id') and a.get('layout')):
        return False
    if _scope(a) == _scope(b) and a.get('stint_id') != b.get('stint_id'):
        return False
    try:
        return gt7_context.compatible(a, b) and gt7_context.compatible(b, a)
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


class _Trace(list):
    def __init__(self, points):
        super().__init__(points)
        self.distances = [point['d'] for point in points]


def _window(trace, start, end):
    distances = trace.distances if isinstance(trace, _Trace) else [p['d'] for p in trace]
    first = max(0, bisect.bisect_left(distances, start) - 1)
    last = min(len(trace), bisect.bisect_left(distances, end) + 2)
    return trace[first:last]


def _trace(row):
    raw = row.get('trace')
    if not isinstance(raw, list) or not 2 <= len(raw) <= TRACE_MAX:
        return []
    output = []
    for point in raw:
        if not isinstance(point, dict) or not all(finite(point.get(k)) for k in ('d', 't')):
            return []
        d, t = point['d'], point['t']
        if not 0 <= d <= gt7_laps.MAX_TRACE_M or t < 0:
            return []
        if output and (not 0 < d - output[-1]['d'] <= 2 * gt7_laps.STEP_M or t <= output[-1]['t']):
            return []
        item = dict(d=d, t=t)
        for key in ('speed_kmh', 'brake', 'throttle', 'steer_deg', 'gear', 'x', 'z'):
            value = point.get(key)
            valid = finite(value)
            if key in ('brake', 'throttle'):
                valid = valid and 0 <= value <= 100
            if key == 'gear':
                valid = valid and type(value) is int and 1 <= value <= 15
            if key == 'speed_kmh':
                valid = valid and value >= 0
            item[key] = value if valid else None
        output.append(item)
    return _Trace(output)


def _duration(trace, start, end):
    window = _window(trace, start, end)
    times = [gt7_laps._time_at(window, d) for d in (start, end)]
    if None in times:
        return None
    duration = times[1] - times[0]
    return duration if finite(duration) and duration > 0 else None


def _corners(definition):
    if not isinstance(definition, dict) or not isinstance(definition.get('corners'), list):
        return []
    out = []
    for row in definition['corners'][:CORNERS_MAX]:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or not 0 < len(row['id']) <= 80
                or not all(finite(row.get(k)) for k in ('entry_m', 'apex_m', 'exit_m'))
                or not 0 <= row['entry_m'] < row['apex_m'] < row['exit_m'] <= gt7_laps.MAX_TRACE_M):
            continue
        out.append({k: row.get(k) for k in ('id', 'entry_m', 'apex_m', 'exit_m', 'number', 'direction', 'name')})
    return sorted(out, key=lambda r: r['entry_m'])


def _metrics(trace, corner):
    start, end = corner['entry_m'], corner['exit_m']
    trace = _window(trace, start, end)
    points = [p for p in trace if start <= p['d'] <= end]
    if len(points) < 2 or _duration(trace, start, end) is None:
        return {}
    channels = driver.channels(trace, [corner])[0]
    metrics = {k: v if finite(v) else None for k, v in channels.items() if k in SCREENS}
    after = next((p for p in trace if p['d'] >= end), None)
    metrics.update(exit_speed_kmh=after.get('speed_kmh') if after else None,
                   exit_gear=after.get('gear') if after else None)
    if all(p.get('speed_kmh') is not None and p.get('brake') is not None for p in trace):
        measured = gt7_track_definitions.corner_metrics({'trace': trace}, {'corners': [corner]})[0]
        metrics.update({k: measured[k] for k in ('time_s', 'entry_speed_kmh', 'min_speed_kmh', 'max_brake_pct')})
    metrics['time_s'] = _duration(trace, start, end)
    return metrics


def _separation(own, other, corner):
    own = _window(own, corner['entry_m'], corner['exit_m'])
    ref = {p['d']: p for p in _window(other, corner['entry_m'], corner['exit_m'])}
    distances = []
    for point in own:
        if not corner['entry_m'] <= point['d'] <= corner['exit_m']:
            continue
        match = ref.get(point['d'])
        if match is None or any(p.get(k) is None for p in (point, match) for k in ('x', 'z')):
            return None
        distances.append(math.hypot(point['x'] - match['x'], point['z'] - match['z']))
    if not distances:
        return None
    mean = sum(distances) / len(distances)
    return mean if finite(mean) else None


def _following_end(corner, corners, definition, trace):
    following = next((row['exit_m'] for row in corners if row['entry_m'] > corner['entry_m']), None)
    end = following if following is not None else (trace[-1]['d'] if trace else None)
    combinations = definition.get('combinations', [])
    if isinstance(combinations, list):
        for combination in combinations[:CORNERS_MAX]:
            ids = combination.get('corner_ids') if isinstance(combination, dict) else None
            if not isinstance(ids, list) or len(ids) > CORNERS_MAX or corner['id'] not in ids:
                continue
            combined = [c['exit_m'] for c in corners if c['id'] in ids]
            if combined:
                end = max([end or 0, *combined])
    return end


def _check(own, reference, corner, end):
    start, exit_m = corner['entry_m'], corner['exit_m']
    if end is None or end <= exit_m:
        return dict(available=False, result='unavailable')
    times = [_duration(trace, a, b) for trace in (own, reference)
             for a, b in ((start, exit_m), (exit_m, end), (start, end))]
    if any(t is None for t in times):
        return dict(available=False, result='unavailable')
    exit_points = [next((p for p in trace if p['d'] >= exit_m), None) for trace in (own, reference)]
    speeds = [point.get('speed_kmh') if point else None for point in exit_points]
    if any(speed is None for speed in speeds):
        return dict(available=False, result='unavailable')
    exit_delta = speeds[0] - speeds[1]
    a, b, total, ar, br, totalr = times
    net, downstream = total - totalr, b - br
    screen = max(.05, totalr * .005)
    result = ('tradeoff' if a < ar and (net > screen or exit_delta < -2) else
              'provisional_gain' if net < -screen and downstream <= screen and exit_delta >= -2 else 'no_clear_change')
    return dict(available=True, start_m=start, end_m=end, corner_delta_s=a-ar,
                downstream_delta_s=downstream, net_delta_s=net, own_s=total, reference_s=totalr,
                screen_s=screen, result=result,
                exit_speed_kmh=speeds[0], reference_exit_speed_kmh=speeds[1], exit_speed_delta_kmh=exit_delta,
                text='Compare repeated entry-to-following-exit time and measured exit speed on comparable attempts.')


def _evidence(row, section, metric, value, unit):
    ident = hashlib.sha256(json.dumps([_scope(row), row['id'], row.get('definition_fingerprint')]).encode()).hexdigest()[:32]
    return dict(id=ident+'/'+section+'/'+metric, value=value, unit=unit,
                lap_id=row['id'], provenance='measured or deterministic distance-station calculation')


def assess(lap, reference=None, definition=None, personal_laps=()):
    """Pure live assessment; caller provides current authorized frozen evidence."""
    unknown = ['Traffic is not observed.', 'Weather is not measured.', 'Tyre wear is not measured.',
               'Setup and aid behavior are not inferred.']
    result = dict(status='unsuitable', lap_id=lap.get('id') if isinstance(lap, dict) else None,
                  reference_id=None, findings=[], unknown=unknown,
                  summary='This lap is unsuitable for a new technique score. Keep the current exercise.')
    if not _eligible(lap):
        return result
    if reference is not None and not isinstance(reference, dict):
        unknown.append('Selected reference is unavailable.')
        reference = None
    rows = personal_laps[-COHORT_MAX:] if isinstance(personal_laps, (list, tuple)) else []
    cohort = {(_scope(row), row['id']): row for row in [*rows, lap] if _compatible(lap, row)}
    candidates = [row for row in cohort.values() if (_scope(row), row['id']) != (_scope(lap), lap['id'])]
    if reference is None or (_scope(reference), reference.get('id')) == (_scope(lap), lap['id']):
        reference = min(candidates, key=lambda r: r['time_s']) if candidates else None
    if reference is not None and not _compatible(lap, reference):
        unknown.append('Selected reference is incompatible with this lap.')
        reference = None
    own_trace = _trace(lap)
    for keys, label in ((('brake',), 'Brake input'), (('throttle',), 'Throttle input'),
                        (('steer_deg',), 'Steering'), (('gear',), 'Gear'),
                        (('speed_kmh',), 'Speed'), (('x', 'z'), 'Line coordinate')):
        if not own_trace or any(p.get(k) is None for p in own_trace for k in keys):
            unknown.append(label+' coverage is incomplete or unavailable.')
    result.update(status='insufficient', reference_id=reference.get('id') if reference else None,
                  summary=f"Completed lap in {lap['time_s']:g} seconds. Repeat a comparable approach to establish consistent evidence.")
    ref_trace = _trace(reference) if reference else []
    valid_definition = (isinstance(definition, dict) and definition.get('fingerprint')
                        and lap.get('definition_fingerprint') == definition['fingerprint']
                        and reference is not None and reference.get('definition_fingerprint') == definition['fingerprint'])
    corners = _corners(definition) if valid_definition else []
    if not corners:
        unknown.append('Comparable corner definition is unavailable.')
    findings = []
    if own_trace and ref_trace:
        trace_cache = {key: _trace(row) for key, row in cohort.items()}
        for corner in corners:
            cache = {}
            def metrics(row, trace, cache=cache, corner=corner):
                key = (_scope(row), row['id'])
                if key not in cache:
                    cache[key] = _metrics(trace, corner)
                return cache[key]
            a, b = metrics(lap, own_trace), metrics(reference, ref_trace)
            a['line_separation_m'], b['line_separation_m'] = _separation(own_trace, ref_trace, corner), 0
            check = _check(own_trace, ref_trace, corner, _following_end(corner, corners, definition, own_trace))
            if not check['available']:
                continue
            reviewed = (definition.get('reviewed') is True and type(corner.get('number')) is int
                        and 0 < corner['number'] <= 10000)
            label = ('Turn '+str(corner['number'])+(': '+str(corner.get('name', ''))[:160] if corner.get('name') else '')
                     if reviewed else 'Provisional section')
            specs = dict(SCREENS, window_time_s=('pace', 's', check['screen_s']))
            a['window_time_s'], b['window_time_s'] = check['own_s'], check['reference_s']
            history = []
            for key, row in cohort.items():
                if (_scope(row), row['id']) == (_scope(reference), reference['id']) or not trace_cache[key]:
                    continue
                prior = metrics(row, trace_cache[key])
                prior['line_separation_m'] = _separation(trace_cache[key], ref_trace, corner)
                prior['window_time_s'] = _duration(trace_cache[key], corner['entry_m'], check['end_m'])
                history.append(prior)
            for metric, (skill, unit, screen) in specs.items():
                observed, ref = a.get(metric), b.get(metric)
                if not finite(observed) or not finite(ref) or abs(observed-ref) < screen:
                    continue
                support = sum(finite(row.get(metric)) and abs(row[metric]-ref) >= screen for row in history)
                evidence = [_evidence(lap, corner['id'], metric, observed, unit),
                            _evidence(reference, corner['id'], metric, ref, unit)]
                for row, seconds, speed in ((lap, check['own_s'], check['exit_speed_kmh']),
                                            (reference, check['reference_s'], check['reference_exit_speed_kmh'])):
                    evidence.extend([_evidence(row, corner['id'], 'entry_to_following_exit_s', seconds, 's'),
                                     _evidence(row, corner['id'], 'exit_speed_kmh', speed, 'km/h')])
                findings.append(dict(id=corner['id']+'/'+metric, section_id=corner['id'], skill=skill,
                                     metric=metric, observed=observed, reference=ref, delta=observed-ref,
                                     unit=unit, label=label, reviewed=reviewed, support=support,
                                     observation=label+': '+LABELS[metric]+' differs from the reference.',
                                     interpretation='Personal variation and demonstrated capability; the difference does not establish its cause.',
                                     action=ACTIONS[skill], check=check, evidence=evidence,
                                     strength=min(3, abs(observed-ref)/screen)))
    findings.sort(key=lambda f: (f['support'], f['strength']), reverse=True)
    if not findings and len(cohort) >= 2:
        times = [row['time_s'] for row in cohort.values()]
        variation = max(times)-min(times)
        findings.append(dict(id='lap/consistency', section_id=None, skill='consistency', metric='lap_range_s',
                             observed=variation, reference=None, delta=None, unit='s', support=len(cohort),
                             reviewed=False, label='Personal lap consistency', strength=1,
                             observation='Comparable personal laps show measured timing variation.',
                             interpretation='Personal repeatability does not establish an optimal driving target.',
                             action=ACTIONS['consistency'], check=dict(available=True, result='provisional',
                                 text='Compare timing variation over repeated comparable attempts.'),
                             evidence=[_evidence(row, 'lap', 'time_s', row['time_s'], 's') for row in cohort.values()]))
    result['findings'] = findings[:FINDINGS_MAX]
    if findings:
        result['status'] = 'ready'
        result['summary'] = f"Completed lap in {lap['time_s']:g} seconds. " + findings[0]['action']
    return result

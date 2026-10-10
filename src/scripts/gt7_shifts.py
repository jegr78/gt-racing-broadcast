#!/usr/bin/env python3
"""Post-session normalized power references and discrete recorded shift phases.

Power equality at equal road speed is P(r) = P(r * next_ratio/current_ratio).
These full-throttle instantaneous-shift references assume sufficient grip, not an
optimum race action or fuel target. Upstream material is explicitly downloaded
into machine-local runtime only; no curves, plots or source implementation ship.
"""
import bisect
import copy
import math
import gt7_context as gc

ALGORITHM = 'piecewise-power-equality-v1'
MAX_ROWS = 240000


def number(value, label, low=0, high=50000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low < value <= high:
        raise ValueError(label+' must be a finite positive number in range')
    return value


def car_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 9999999:
        raise ValueError('car ID must be a positive integer')
    return value


def validate_curve(points):
    gc._list(points)
    if not 2 <= len(points) <= 2000:
        raise ValueError('curve requires 2 to 2000 samples')
    previous = 0
    for point in points:
        gc._object(point)
        gc._keys(point, {'rpm', 'power', 'torque'})
        rpm = number(point.get('rpm'), 'curve RPM')
        if rpm <= previous:
            raise ValueError('curve RPM must be strictly increasing')
        previous = rpm
        for key in ('power', 'torque'):
            number(point.get(key), 'normalized '+key, -1e-9, 100.01)
    if any(abs(max(p[key] for p in points)-100) > .01 for key in ('power', 'torque')):
        raise ValueError('power and torque must each be normalized to a maximum of 100')
    return copy.deepcopy(points)


def validate_ratios(ratios):
    gc._list(ratios)
    if not 2 <= len(ratios) <= 8:
        raise ValueError('ratios require 2 to 8 forward gears')
    for r in ratios:
        number(r, 'gear ratio', high=30)
    if any(b >= a for a, b in zip(ratios, ratios[1:], strict=False)):
        raise ValueError('adjacent forward ratios must decrease')
    return list(ratios)


def interpolate(points, rpm):
    xs = [p['rpm'] for p in points]
    if not math.isfinite(rpm) or rpm < xs[0]-1e-8 or rpm > xs[-1]+1e-8:
        return None
    rpm = min(xs[-1], max(xs[0], rpm))
    i = bisect.bisect_left(xs, rpm)
    if xs[i] == rpm:
        return points[i]['power']
    a, b = points[i-1:i+1]
    return a['power']+(b['power']-a['power'])*(rpm-a['rpm'])/(b['rpm']-a['rpm'])


def calculate(points, ratios):
    points, ratios = validate_curve(points), validate_ratios(ratios)
    xs = [p['rpm'] for p in points]
    peak = max(points, key=lambda p: p['power'])['rpm']
    out = []
    for gear, (a, b) in enumerate(zip(ratios, ratios[1:], strict=False), 1):
        q = b/a
        low = max(peak, xs[0]/q)
        if low > xs[-1]:
            out.append({'gear': gear, 'rpm': None, 'status': 'no-overlap', 'crossings_rpm': []})
            continue
        bounds = sorted({low, xs[-1], *[r for r in xs if low <= r <= xs[-1]],
                         *[r/q for r in xs if low <= r/q <= xs[-1]]})
        roots = []
        diff = lambda r, q=q: interpolate(points, r)-interpolate(points, r*q)
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            dl, dh = diff(lo), diff(hi)
            if dl > 1e-9 and dh <= 0:
                roots.append(lo+dl/(dl-dh)*(hi-lo))
            elif abs(dl) <= 1e-9 and dh < -1e-9:
                roots.append(lo)
        roots = sorted(set(round(r, 8) for r in roots))
        rpm = roots[0] if roots else xs[-1]
        out.append({'gear': gear, 'rpm': rpm, 'ratio_factor': q,
                    'status': 'multiple-crossings' if len(roots) > 1 else 'crossing' if roots else 'range-capped',
                    'crossings_rpm': roots, 'power_difference': diff(rpm)})
    return out


def manual_reference(value):
    return gc.validate_shift_reference(value)


def _driving(row):
    return (row.get('rpm') is not None and row.get('throttle_pct') is not None
            and row['throttle_pct'] >= 90 and row.get('clutch_engagement') is not None
            and row['clutch_engagement'] >= .95)


def detect_shifts(rows):
    """Bracket stable drive -> cut -> engaged new gear; missing phase stays unknown."""
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError('shift observation exceeds the packet bound')
    out = []
    for i in range(1, len(rows)):
        old, new = rows[i-1], rows[i]
        a, b = old.get('gear'), new.get('gear')
        bridged = a in (None, 0)
        if bridged and isinstance(b, int) and b > 0:
            known = next((r for r in reversed(rows[max(0, i-40):i])
                          if new['t']-.6 <= r['t'] < new['t'] and isinstance(r.get('gear'), int) and r['gear'] > 0), None)
            a = known['gear'] if known else None
            old = known or old
        if not isinstance(a, int) or not isinstance(b, int) or not 1 <= a < b <= 15:
            continue
        t = new['t']
        before = [r for r in rows[max(0, i-30):i] if t-.4 <= r['t'] < t and r.get('gear') == a]
        after = [r for r in rows[i:min(len(rows), i+40)] if t <= r['t'] <= t+.6]
        contiguous = (not bridged and b == a+1 and len(before) >= 3 and
                      all(0 < y['t']-x['t'] <= .06 for x, y in zip(before+[new], (before+[new])[1:], strict=False)))
        candidates = [(j, r) for j, r in enumerate(before) if _driving(r) and j >= 1
                      and _driving(before[j-1]) and r['rpm'] >= before[j-1]['rpm']-25]
        pre = max(candidates, key=lambda pair: pair[1]['rpm'])[1] if contiguous and candidates else None
        # Require a recorded departure from the driving state or a clear RPM drop before/at the displayed edge.
        cut = pre and any(r['t'] > pre['t'] and (not _driving(r) or r['rpm'] < pre['rpm']-25)
                          for r in before+[new])
        if not cut:
            pre = None
        post = None
        if contiguous:
            for x, y in zip(after, after[1:], strict=False):
                if not 0 < y['t']-x['t'] <= .06:
                    break
                if (x.get('gear') == y.get('gear') == b and _driving(x) and _driving(y)
                        and 0 < y['t']-x['t'] <= .06):
                    post = x
                    break
                if x.get('gear') != b:
                    break
        out.append({'from_gear': a, 'to_gear': b, 'gear_change_t_s': t,
                    'last_old_gear_rpm': old.get('rpm'),
                    'pre_cut_rpm': pre['rpm'] if pre else None, 'pre_cut_t_s': pre['t'] if pre else None,
                    'post_rpm': post['rpm'] if post else None, 'post_t_s': post['t'] if post else None,
                    'phase_status': 'measured' if pre and post else 'ambiguous/missing',
                    'neutral_bridge': bridged,
                    'phase_method': 'Stable full-drive samples before cut; first engaged new-gear bracket. Sampled phase inference.'})
    return out


def reference(external, manual, car, confirmed, observed_ratios=None):
    warnings = []
    external = external if external and external.get('car_id') == car else {}
    if manual is not None:
        manual = manual_reference(manual)
        if manual['car_id'] == car:
            return {'kind': 'manual-event', 'car_id': car, 'event_confirmed': bool(confirmed and manual['confirmed']),
                    'targets': [{'gear': int(g), 'rpm': rpm, 'status': 'manual'} for g, rpm in sorted(manual['targets'].items(), key=lambda p: int(p[0]))],
                    'configuration': manual['configuration'], 'provenance': manual['provenance'],
                    'curve': external.get('curve'), 'curve_event_confirmed': False,
                    'curve_units': 'External power and torque each normalized to maximum 100; external curve unverified for this event',
                    'curve_range_rpm': [external['curve'][0]['rpm'], external['curve'][-1]['rpm']] if external.get('curve') else None,
                    'source': external.get('source'), 'source_ratios': external.get('ratios'),
                    'observed_ratios': observed_ratios, 'ratio_source': 'decoded transmission' if observed_ratios else 'unavailable',
                    'revbar_rpm': external.get('revbar_rpm'), 'algorithm': 'manual-event-table',
                    'rev_limit_rpm': manual.get('rev_limit_rpm') if confirmed and manual['confirmed'] else None,
                    'snapshot': manual, 'warnings': warnings}
        warnings.append('Manual event reference belongs to a different vehicle')
    curve = external.get('curve')
    ratios = observed_ratios or external.get('ratios')
    targets = calculate(curve, ratios) if curve and ratios else []
    source_ratios = external.get('ratios')
    match = (len(observed_ratios) == len(source_ratios) and
             all(abs(a-b) < .001 for a, b in zip(observed_ratios, source_ratios, strict=True))) if observed_ratios and source_ratios else None
    warnings += ['External stock/BoP curve is unverified for this event. Matching ratios do not confirm power shape.',
                 'Full-throttle, sufficient-grip, instantaneous-shift reference; earlier shifts can save fuel or manage traction.']
    return {'kind': 'external' if external else 'unavailable', 'car_id': car, 'event_confirmed': False,
            'curve': curve, 'curve_units': 'Power and torque each normalized to maximum 100; no HP/kW/Nm',
            'curve_range_rpm': [curve[0]['rpm'], curve[-1]['rpm']] if curve else None,
            'targets': targets, 'ratio_source': 'decoded transmission' if observed_ratios else 'external stock' if ratios else 'unavailable',
            'observed_ratios': observed_ratios, 'source_ratios': source_ratios, 'ratios_match': match,
            'support': {'curve': bool(curve), 'source_ratios': bool(source_ratios), 'observed_ratios': bool(observed_ratios),
                        'targets': bool(targets) and all(t['rpm'] is not None for t in targets)},
            'revbar_rpm': external.get('revbar_rpm'), 'rev_limit_rpm': None,
            'source': external.get('source'), 'algorithm': ALGORITHM, 'warnings': warnings}


def analyse(rows, lap, external=None, manual=None, session_confirmed=False, observed_ratios=None):
    ref = reference(external, manual, lap.get('car_id'), session_confirmed, observed_ratios)
    targets = {s['gear']: s['rpm'] for s in ref['targets']}
    events = detect_shifts(rows)
    for event in events:
        absolute = lap.get('start_t_s', 0)+event['gear_change_t_s']
        members = [m for m in lap.get('memberships', []) if m.get('start_t_s') is not None
                   and m.get('end_t_s') is not None and m['start_t_s'] <= absolute < m['end_t_s']]
        member = members[0] if len(members) == 1 else {}
        strategy = member.get('strategy', {}) if member else lap.get('strategy', {}) if not lap.get('memberships') else {}
        economy = strategy.get('targets', {}).get(str(event['from_gear']))
        target = targets.get(event['from_gear'])
        pre = event['pre_cut_rpm']
        event.update(rec=lap.get('rec'), session=lap.get('session'), lap=lap.get('lap'),
                     stint_id=member.get('stint_id'), compound=member.get('compound'),
                     intentional_shortshift=strategy.get('shortshift'),
                     acceleration_target_rpm=target, economy_target_rpm=economy,
                     acceleration_deviation_rpm=pre-target if pre is not None and target is not None else None,
                     economy_deviation_rpm=pre-economy if pre is not None and economy is not None else None)
    return {'reference': ref, 'shifts': events}

SOURCE = 'https://github.com/theRTB/GT7ShiftTone'
SOURCE_API = 'https://api.github.com/repos/theRTB/GT7ShiftTone/commits/main'
SOURCE_RAW = 'https://raw.githubusercontent.com/theRTB/GT7ShiftTone/'
MAX_DOWNLOAD = 2*1024*1024


def _download(url):
    import http_util
    with http_util.open_url(url, timeout=10) as response:
        data = response.read(MAX_DOWNLOAD+1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError('shift source response exceeds the download bound')
    return data


class Cache:
    """Only explicit updates access the fixed source; failed updates keep the old cache."""
    def __init__(self, runtime_base):
        import os
        self.root = os.path.join(os.path.abspath(runtime_base), 'gt7-shift-references')

    def path(self, car):
        import os
        return os.path.join(self.root, str(car_id(car))+'.json')

    def read(self, car):
        value = gc._load(self.path(car))
        if value is None:
            return None
        gc._object(value)
        gc._bounded(value)
        gc._keys(value, {'format', 'version', 'car_id', 'curve', 'ratios', 'revbar_rpm', 'source'})
        if value.get('format') != 'racecast-shift-source' or value.get('version') != 1 or value.get('car_id') != car:
            raise ValueError('shift cache identity or format mismatch')
        if value.get('curve') is not None:
            validate_curve(value['curve'])
        if value.get('ratios') is not None:
            validate_ratios(value['ratios'])
        if value.get('revbar_rpm') is not None:
            number(value['revbar_rpm'], 'display revbar RPM')
        source = gc._object(value.get('source'))
        if source.get('identity') != SOURCE:
            raise ValueError('unknown shift source')
        import re
        if not re.fullmatch('[0-9a-f]{40}', str(source.get('commit'))):
            raise ValueError('invalid shift source revision')
        gc._text(source.get('fetched_at'), 100)
        return copy.deepcopy(value)

    def update(self, car):
        import csv
        import datetime
        import hashlib
        import io
        import json
        import re
        import http_util
        car_id(car)
        commit = json.loads(_download(SOURCE_API)).get('sha')
        if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
            raise ValueError('invalid upstream commit identity')
        base = SOURCE_RAW+commit+'/'
        try:
            curve_bytes = _download(base+'curves/stock/'+str(car)+'.tsv')
        except http_util.HTTPError as exc:
            if exc.code != 404:
                raise
            curve_bytes = None
        ratio_bytes = _download(base+'database/stock_ratios.json')
        revbar_bytes = _download(base+'database/revbar.json')
        curve = None
        if curve_bytes is not None:
            reader = csv.DictReader(io.StringIO(curve_bytes.decode('utf-8')), delimiter='\t')
            if reader.fieldnames != ['rpm', 'power', 'torque']:
                raise ValueError('unsupported shift curve columns')
            curve = validate_curve([{k: float(v) for k, v in row.items()} for row in reader])
        ratio_entry = gc._object(json.loads(ratio_bytes)).get(str(car))
        ratios = validate_ratios(gc._object(ratio_entry).get('ratios')) if ratio_entry is not None else None
        revbar = gc._object(json.loads(revbar_bytes)).get(str(car))
        if revbar is not None:
            number(revbar, 'display revbar RPM')
        value = {'format': 'racecast-shift-source', 'version': 1, 'car_id': car,
                 'curve': curve, 'ratios': ratios, 'revbar_rpm': revbar,
                 'source': {'identity': SOURCE, 'commit': commit,
                            'fetched_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            'hashes': {k: hashlib.sha256(v).hexdigest() if v is not None else None
                                       for k, v in [('curve', curve_bytes), ('ratios', ratio_bytes), ('revbar', revbar_bytes)]},
                            'measurement_revision': None,
                            'assumptions': ['Stock/BoP source', 'Full throttle', 'Sufficient grip', 'Instantaneous shifts',
                                            'No per-curve measurement date, GT7 revision or fuel map supplied'],
                            'redistribution': 'Not bundled; explicitly fetched into local runtime'}}
        gc._bounded(value)
        path = self.path(car)
        with gc._LOCK, gc._file_lock(path):
            gc._atomic(path, value)
        return self.read(car)


def recorded_rows(path, index, lap):
    """Read bounded original packets in sparse-seek chunks, never distance samples."""
    import gt7_channel_detail as detail
    duration = lap.get('end_t_s', 0)-lap.get('start_t_s', 0)
    number(duration, 'observed lap duration', high=3600)
    rows, ratio_sets = [], []
    keys = ['rpm', 'gear', 'throttle_pct', 'clutch_engagement', 'gearbox_rpm', 'alert_min_rpm', 'alert_max_rpm']
    for start in range(0, math.ceil(duration), 30):
        end = min(start+30, duration)
        part = detail.window(path, index, lap, keys, 'time', start, end)
        for row in part['rows']:
            if not rows or row['t'] > rows[-1]['t']:
                rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ValueError('shift observation exceeds the packet bound')
        values = detail.window(path, index, lap, ['gear_ratio_'+str(g) for g in range(1, 9)],
                               'time', start, min(start+.1, duration))['rows']
        for row in values:
            ratios = []
            for gear in range(1, 9):
                r = row['gear_ratio_'+str(gear)]
                if r is None:
                    break
                ratios.append(r)
            if len(ratios) >= 2:
                ratios = validate_ratios(ratios)
                if not any(len(ratios) == len(other) and all(abs(a-b) < .001 for a, b in zip(ratios, other, strict=True))
                           for other in ratio_sets):
                    ratio_sets.append(ratios)
    return {'rows': rows, 'observed_ratios': ratio_sets[0] if len(ratio_sets) == 1 else None,
            'ratio_warnings': ['Decoded gearbox differs within this lap'] if len(ratio_sets) > 1 else [],
            'display_alerts': {k: rows[0].get(k) if rows else None for k in ('alert_min_rpm', 'alert_max_rpm')}}

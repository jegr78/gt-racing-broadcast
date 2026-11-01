"""Pure bounded driver-radio facts. No inference, I/O or provider dependencies."""

import math
import statistics

import gt7_context

PRESETS = ('fuel', 'pace', 'tyres', 'status')
DEFAULTS = dict(fuel_warning_laps=2.0, fuel_urgent_laps=1.0, consumption_laps=5,
                baseline_laps=3, recent_laps=3, pace_min_s=.5, pace_fraction=.005,
                noise_multiplier=3.0)
MAX_ROWS = 512
MIN_PACE_LAPS = 3
TO_GO_MARKS = (10, 5, 4, 3, 2, 1)


def number(value, positive=False):
    if type(value) not in (int, float) or not -1e12 <= value <= 1e12:
        return None
    return value if math.isfinite(value) and (not positive or value >= 1e-6) else None


def settings(overrides=None):
    if overrides is not None and (not isinstance(overrides, dict) or set(overrides) - set(DEFAULTS)):
        raise ValueError('Unsupported radio assessment limits')
    result = dict(DEFAULTS, **(overrides or {}))
    for key, value in result.items():
        if number(value, True) is None or value > 100:
            raise ValueError('Radio limits must be finite positive numbers up to 100')
        if key.endswith('_laps') and key not in ('fuel_warning_laps', 'fuel_urgent_laps'):
            if type(value) is not int or not 2 <= value <= 32:
                raise ValueError('Radio sample windows must contain 2 to 32 laps')
    if result['fuel_urgent_laps'] >= result['fuel_warning_laps']:
        raise ValueError('Urgent fuel limit must be below warning limit')
    return result


def binding(snapshot):
    if snapshot.get('session_id') is None:
        return None
    return {key: snapshot.get(key) for key in ('generation', 'session_id', 'context_revision')}


def _same_context(a, b):
    if any(a.get(k) is not None and b.get(k) is not None and a[k] != b[k]
           for k in ('car_id', 'track_id', 'definition_fingerprint', 'sector_variant_id')):
        return False
    return gt7_context.compatible(a, dict(b, comparison_eligible=True)) and gt7_context.compatible(b, dict(a, comparison_eligible=True))


def _cohort(snapshot, service):
    current = snapshot.get('current') or {}
    rows = snapshot.get('laps') or []
    if current.get('after_service'):
        return []
    service_lap = next((r.get('lap') for r in rows[-MAX_ROWS:] if r.get('id') == service.get('lap_id')), None)
    if service.get('lap_id') == current.get('id'):
        service_lap = current.get('lap')
    selected = []
    for row in reversed(rows[-MAX_ROWS:]):
        if row.get('session_id') != snapshot.get('session_id'):
            break
        if not _same_context(current, row) or any(not _same_context(old, row) for old in selected):
            break
        if row.get('after_service'):
            break
        if service:
            # A late declaration may refer to a lap evicted from the bounded history.
            # In that case only a proven post-confirmation timestamp may qualify.
            if service_lap is not None:
                if row.get('lap', 0) <= service_lap + 1:
                    break
            elif number(row.get('_start_wall')) is None or row['_start_wall'] <= service.get('confirmed_at', float('inf')):
                break
        if (row.get('state') != 'confirmed' or not row.get('comparison_eligible')
                or row.get('capture_complete') is False
                or row.get('analysis_role') in ('first', 'pit', 'warmup', 'partial')):
            continue
        selected.append(row)
        if len(selected) >= 64:
            break
    return list(reversed(selected))


def _pace(rows, limits):
    rows = [r for r in rows if number(r.get('time_s'), True) is not None]
    # Early in a stint the windows share the laps there are, up to their limits.
    base_count, recent_count = limits['baseline_laps'], limits['recent_laps']
    if len(rows) < base_count + recent_count:
        recent_count = min(recent_count, max(1, len(rows)//2))
        base_count = min(base_count, len(rows)-recent_count)
    result = dict(state='insufficient', last_s=rows[-1]['time_s'] if rows else None,
                  best_s=min((r['time_s'] for r in rows), default=None),
                  baseline_ids=[], recent_ids=[], delta_s=None, threshold_s=None)
    if len(rows) < MIN_PACE_LAPS:
        return result
    base = rows[-base_count-recent_count:-recent_count]
    recent = rows[-recent_count:]
    baseline = statistics.median(r['time_s'] for r in base)
    latest = statistics.median(r['time_s'] for r in recent)
    mad = statistics.median(abs(r['time_s'] - baseline) for r in base)
    threshold = max(limits['pace_min_s'], baseline * limits['pace_fraction'], mad * limits['noise_multiplier'])
    delta = latest - baseline
    repeats = sum(r['time_s'] - baseline >= threshold for r in recent)
    # A slow window whose newest lap is back on pace is over, not a trend to report.
    state = ('slower' if delta >= threshold and repeats >= math.ceil(recent_count * 2/3)
             and recent[-1]['time_s'] - baseline >= threshold
             else 'faster' if delta <= -threshold else 'stable')
    return dict(result, state=state, repeated=repeats >= 2, baseline_ids=[r['id'] for r in base],
                recent_ids=[r['id'] for r in recent], baseline_s=round(baseline, 3),
                recent_s=round(latest, 3), delta_s=round(delta, 3), threshold_s=round(threshold, 3))


def _latest_lap(snapshot):
    """The newest confirmed timed lap of this session, comparable or not: a lap update
    also reports the out lap, which never enters the pace comparison."""
    for row in reversed((snapshot.get('laps') or [])[-MAX_ROWS:]):
        if row.get('session_id') != snapshot.get('session_id'):
            return None
        if row.get('state') == 'confirmed' and number(row.get('time_s'), True) is not None:
            return row
    return None


def _lap_clock(seconds):
    """A lap time as drivers read it: 1:23.467, or 58.123 seconds under a minute."""
    minutes, rest = divmod(round(seconds, 3), 60)
    return f"{int(minutes)}:{rest:06.3f}." if minutes else f"{rest:.3f} seconds."


def _counts_for_fastest(row):
    return (row.get('state') == 'confirmed' and row.get('comparison_eligible') and row.get('capture_complete') is not False
            and row.get('analysis_role') not in ('first', 'pit', 'warmup', 'partial')
            and number(row.get('time_s'), True) is not None)


def _is_fastest(latest, snapshot):
    """The newest lap beats every earlier one of the whole session. Unlike the pace
    comparison, a tyre stop starts nothing new here. A first counted lap has nothing to
    beat, and a lap outside the comparison (out lap, pit, cut) never counts."""
    if not _counts_for_fastest(latest):
        return False
    times = [r['time_s'] for r in (snapshot.get('laps') or [])[-MAX_ROWS:]
             if r.get('session_id') == snapshot.get('session_id') and r.get('id') != latest.get('id') and _counts_for_fastest(r)]
    return bool(times) and latest['time_s'] < min(times)


def _position(snapshot, live):
    value = number((snapshot.get('instruments') or {}).get('race_position'), True)
    return f'P{value:g}' if live and value is not None else None


def _to_go(total, completed, milestones_only=False):
    """Laps left in a race with a declared length, counted from the completed laps."""
    left = int(total-completed)
    if left < 1 or (milestones_only and left not in TO_GO_MARKS):
        return None
    return 'Final lap.' if left == 1 else f'{left} to go.'


def _finish_text(position, best_s):
    text = f'finished {position}' if position else 'finished'
    return text+(f', best lap: {_lap_clock(best_s)}' if best_s is not None else '.')


def _lap_text(row, usable, fastest, position, to_go=None):
    minutes, seconds = divmod(row['time_s'], 60)
    text = f"{int(minutes)}:{seconds:04.1f}"
    if number(row.get('lap'), True) is not None:
        text = f"Lap {row['lap']:g}, {text}"
    text += ', your fastest lap.' if fastest else '.'
    if position:
        text += f' {position}.'
    if to_go:
        text += f' {to_go}'
    return text+(f' Fuel for {usable:.1f} laps.' if usable is not None else '')


def _finish(snapshot, setup, rows, consumption, usable):
    current = snapshot.get('current') or {}
    lap = current.get('lap')
    result = dict(finish='unknown', remaining_race_laps=None, finish_assumption=None)
    total = number(setup.get('laps'), True)
    if total is not None and number(lap, True) is not None and lap > total:
        return dict(result, finish='complete', remaining_race_laps=0)
    if not current.get('observed_from_line') or number(lap, True) is None:
        return result
    if total is not None:
        lengths = [r['length_m'] for r in rows if number(r.get('length_m'), True) is not None]
        distance = number(current.get('distance_m'))
        length = statistics.median(lengths) if lengths else None
        if length is None or distance is None or not 0 <= distance <= length * 1.2:
            return result
        remaining = max(0, total - lap + 1 - min(1, distance / length))
        estimated, assumption = False, 'progress estimated from completed comparable lap lengths'
    elif number(setup.get('duration_min'), True) is not None:
        all_rows = [r for r in (snapshot.get('laps') or [])[-MAX_ROWS:] if r.get('session_id') == snapshot.get('session_id')]
        elapsed = number(current.get('elapsed_s'))
        if (not all_rows or len(all_rows) != lap - 1 or any(r.get('lap') != n for n,r in enumerate(all_rows, 1))
                or elapsed is None or any(number(r.get('time_s'), True) is None for r in all_rows)):
            return result
        times = [r['time_s'] for r in rows if number(r.get('time_s'), True) is not None]
        if not times:
            return result
        elapsed += sum(r['time_s'] for r in all_rows)
        remaining = max(0, setup['duration_min'] * 60 - elapsed) / statistics.median(times) + 1
        estimated, assumption = True, 'one extra lap after timer expiry'
    else:
        return result
    result.update(remaining_race_laps=round(remaining, 3), finish_assumption=assumption)
    if consumption is not None and usable is not None:
        sufficient = usable >= remaining
        result['finish'] = ('estimated_' if estimated else '') + ('supported' if sufficient else 'short')
    return result


def assess(snapshot, session=None, limits=None):
    """Return allowlisted facts/English text for one immutable shared snapshot."""
    limits = settings(limits)
    session = session or {}
    stale_declarations = bool(session.get('setup') or session.get('service')) and (
        binding(snapshot) is None or session.get('binding') != binding(snapshot))
    if stale_declarations:
        session = {}
    setup, service = session.get('setup') or {}, session.get('service') or {}
    live = (snapshot.get('receipt', {}).get('state') == 'live'
            and not snapshot.get('replay', {}).get('active') and snapshot.get('session_id') is not None)
    rows = _cohort(snapshot, service)
    recent = []
    for row in rows:
        value = number(row.get('fuel_burn_l'), True)
        if row.get('fuel_burn_l') is None:
            value = number(row.get('fuel_used_l'), True)
        if value is not None:
            recent.append((row['id'], value))
    recent = recent[-limits['consumption_laps']:]
    consumption = statistics.median(v for _, v in recent) if recent else None
    instruments = snapshot.get('instruments') or {}
    fuel = number(instruments.get('fuel_l')) if live else None
    if fuel is not None and fuel < 0:
        fuel = None
    reserve = setup.get('fuel_reserve_l') or 0
    physical = number(fuel / consumption) if fuel is not None and consumption else None
    usable = number(max(0, fuel - reserve) / consumption) if fuel is not None and consumption else None
    finish = _finish(snapshot, setup, rows, consumption, usable)
    severity = ('urgent' if usable is not None and usable <= limits['fuel_urgent_laps']
                else 'warning' if usable is not None and usable <= limits['fuel_warning_laps'] else 'normal')
    if finish['finish'] == 'complete' or finish['finish'] == 'supported' and len(recent) >= 3:
        severity = 'normal'
    if not live:
        severity = 'normal'
        finish = dict(finish, finish='unknown', remaining_race_laps=None)
    fuel_result = dict(current_l=fuel, consumption_l=round(consumption, 3) if consumption else None,
                       range_laps=round(physical, 3) if physical is not None else None,
                       usable_laps=round(usable, 3) if usable is not None else None,
                       reserve_l=reserve, sample_ids=[i for i, _ in recent], samples=len(recent),
                       state='available' if live and fuel is not None and len(recent) >= 3 else 'provisional' if live and fuel is not None and recent else 'unavailable',
                       session_average_l=number(snapshot.get('fuel_avg_l')), severity=severity, **finish)
    pace = _pace(rows, limits)
    temps = instruments.get('tyres_c') or []
    temps = [number(v) if live else None for v in temps[:4]]
    temps += [None] * (4-len(temps))
    compound = service.get('compound') or (snapshot.get('current') or {}).get('compound') or setup.get('starting_compound')
    tyres = dict(temperatures_c=temps, order=['front_left','front_right','rear_left','rear_right'],
                 compound=compound, age_laps=(snapshot.get('current') or {}).get('tyre_age_laps') if not service else None,
                 wear=None, concern=pace['state']=='slower' and pace['repeated'] and live)
    uncertainty = ['Tyre wear, traffic and weather causes are not measured.']
    if not (snapshot.get('current') or {}).get('context_confirmed'):
        uncertainty.append('Current strategy and tyre context may be incomplete.')
    if stale_declarations:
        uncertainty.append('Obsolete or unbound race declarations were discarded.')
    if not setup:
        uncertainty.append('Race length and reserve are not declared.')
    if fuel is None:
        fuel_text = 'Live fuel is unavailable.'
    elif physical is None:
        fuel_text = f'Fuel {fuel:.1f} litres. Range is unavailable until comparable consumption is measured.'
    else:
        fuel_text = (f'Fuel {fuel:.1f} litres. Consumption {consumption:.2f} litres per lap from {len(recent)} comparable laps. '
                     f'Estimated range {physical:.1f} laps, {usable:.1f} after reserve.')
        if len(recent) < 3:
            fuel_text += ' This is provisional.'
        if finish['remaining_race_laps'] is not None:
            fuel_text += f" Estimated finish distance {finish['remaining_race_laps']:.1f} laps."
        if finish['finish'] in ('supported','estimated_supported'):
            fuel_text += ' Range supports the estimated finish after reserve.'
        elif finish['finish'] in ('short','estimated_short'):
            fuel_text += ' Range is short of the estimated finish after reserve.'
        if finish['finish_assumption']:
            fuel_text += (' Assumes one extra lap after timer expiry.' if finish['finish'].startswith('estimated_')
                          else ' Progress uses comparable lap lengths.')
    if finish['finish']=='complete':
        fuel_text += ' The declared race distance is complete.'
    if not live:
        pace_text = 'Live pace assessment is unavailable. Historical lap evidence remains available.'
    elif pace['state']=='insufficient':
        pace_text = 'Not enough comparable completed laps for a pace trend.'
    else:
        pace_text = f"Recent comparable pace is {abs(pace['delta_s']):.1f} seconds {pace['state']}."
        if pace['state']=='stable':
            pace_text = 'Recent comparable pace is stable within the measured noise margin.'
    if live:
        prefix = []
        if pace['last_s'] is not None:prefix.append(f"Last eligible comparable lap {_lap_clock(pace['last_s'])}")
        if pace['best_s'] is not None:prefix.append(f"Best eligible comparable lap {_lap_clock(pace['best_s'])}")
        pace_text = ' '.join([*prefix,pace_text])
    temperature_text = ', '.join(f'{label} {value:.0f} C' if value is not None else label+' unavailable'
                                 for label,value in zip(('front left','front right','rear left','rear right'),temps, strict=True))
    age = number(tyres['age_laps'])
    age_text = f' Confirmed tyre age {age:g} completed laps.' if age is not None and age >= 0 else ' Confirmed tyre age is unavailable.'
    tyre_text = 'Tyre temperatures: '+temperature_text+'. Compound '+(compound or 'unknown')+'.'+age_text
    status_fuel = (f'Fuel {fuel:.1f} litres, usable range {usable:.1f} laps.' if fuel is not None and usable is not None
                   else f'Fuel {fuel:.1f} litres; range unavailable.' if fuel is not None else 'Live fuel is unavailable.')
    if fuel_result['state']=='provisional':status_fuel += ' Fuel estimate is provisional.'
    if finish['finish'] in ('short','estimated_short'):status_fuel += ' Range is short for the estimated finish.'
    status_pace = ('Live pace is unavailable.' if not live else 'Pace trend is not yet available.' if pace['state']=='insufficient'
                   else 'Comparable pace is stable.' if pace['state']=='stable'
                   else f"Comparable pace is {abs(pace['delta_s']):.1f} seconds {pace['state']}.")
    lap = _latest_lap(snapshot)
    fastest = lap is not None and _is_fastest(lap, snapshot)
    position = _position(snapshot, live)
    total = number(setup.get('laps'), True)
    completed = number(lap.get('lap'), True) if lap else None
    race_laps = total is not None and completed is not None
    status_go = _to_go(total, completed) if race_laps and live else None
    status_text = ((f'{position}. ' if position else '')+(f'{status_go} ' if status_go else '')+status_fuel+' '+status_pace
                   +(' Last lap was your fastest.' if fastest and live else ''))
    best = [r['time_s'] for r in (snapshot.get('laps') or [])[-MAX_ROWS:]
            if r.get('session_id') == snapshot.get('session_id') and _counts_for_fastest(r)]
    answers = dict(fuel=fuel_text, pace=pace_text, tyres=tyre_text, status=status_text)
    return dict(version=1, binding=binding(snapshot), live=live, fuel=fuel_result, pace=pace,
                tyres=tyres, uncertainties=uncertainty, limits=limits, latest_lap_id=lap['id'] if lap else None,
                position=position, race_finished=race_laps and completed >= total, completed_laps=completed,
                finish_text=_finish_text(position, min(best) if best else None),
                lap_text=_lap_text(lap, usable, fastest, position, _to_go(total, completed, True) if race_laps else None) if lap else None, answers=answers)

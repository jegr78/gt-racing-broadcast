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
# GT7 telemetry does not say which session runs; the driver declares it.
SESSIONS = ('practice', 'qualifying', 'race')
# GT7 lap 1 is no race lap: a formation lap, or the start lap to the box in a follow-up lobby.
OPENING_LAPS = ('formation', 'to_box')


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
        if row.get('after_service') or row.get('non_race'):
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


def _clock(seconds):
    """A lap time as drivers read it: 1:23.467, or 58.123 seconds under a minute."""
    minutes, rest = divmod(round(seconds, 3), 60)
    return f"{int(minutes)}:{rest:06.3f}" if minutes else f"{rest:.3f} seconds"


def _lap_clock(seconds):
    return _clock(seconds)+'.'


def _counts_for_fastest(row):
    return (row.get('state') == 'confirmed' and row.get('comparison_eligible') and row.get('capture_complete') is not False
            and not row.get('non_race')
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


def _lap_answer(row, fastest, live):
    subject='fastest recorded lap' if fastest else 'last eligible lap'
    if not live or row is None:
        return f'Your {subject} time is unavailable for this session.'
    minutes,remainder=divmod(round(row['time_s']*1000),60000)
    seconds,millis=divmod(remainder,1000)
    text=f'Your {subject} is {minutes}:{seconds:02d}.{millis:03d}'
    lap=number(row.get('lap'),True)
    return text+(f', lap {lap:g}.' if lap is not None else '.')


COMPOUND_NAMES = dict(CH='comfort hard', CM='comfort medium', CS='comfort soft', SH='sports hard', SM='sports medium',
                      SS='sports soft', RH='racing hard', RM='racing medium', RS='racing soft', IM='intermediate',
                      W='wet', D='dirt', S='snow')


def _quick_answers(snapshot, live, position, total, completed, usable, consumption, compound, tyre_age, eligible, service,
                   kind=None):
    """Fixed answers for frequent driver questions: no agent, so they come at once."""
    def sentence(text):
        return text[0].upper()+text[1:]+'.'
    answers = dict(position=f'{position}.' if position else 'Position is not available.')
    if kind in ('practice', 'qualifying'):
        answers['to_go'] = f'No race distance in {"practice" if kind == "practice" else "qualifying"}.'
    elif total is None:
        answers['to_go'] = 'Race length is not set.'
    elif completed is not None and completed >= total:
        answers['to_go'] = 'The race is complete.'
    else:
        answers['to_go'] = sentence(_to_go(total, completed or 0))
    if usable is None:
        answers['fuel_finish'] = 'Fuel range is not measured yet.'
    else:
        parts = [f'fuel for {usable:.1f} laps']
        recent = [r['time_s'] for r in eligible[-3:]]
        if recent:
            minutes = usable*statistics.median(recent)/60
            parts.append('under a minute' if minutes < 1 else f'about {round(minutes):d} minute{"" if round(minutes) == 1 else "s"}')
        if total is not None and completed is not None and completed < total:
            left = total-completed
            parts.append(_to_go(total, completed))
            parts.append('you are fine' if usable >= left else f'{left-usable:.1f} laps short')
        answers['fuel_finish'] = sentence(', '.join(parts))
    answers['consumption'] = (f'{consumption:.1f} litres per lap.' if live and consumption
                              else 'Fuel per lap is not measured yet.')
    name = COMPOUND_NAMES.get(compound)
    if tyre_age is None:
        answers['tyre_age'] = f'{sentence(name)[:-1]}, age not known.' if name else 'Tyre age is not known.'
    else:
        age = f'{tyre_age:g} lap{"" if tyre_age == 1 else "s"} old'
        answers['tyre_age'] = sentence(f'{name}, {age}' if name else f'tyres are {age}')
    rows = [r for r in (snapshot.get('laps') or [])[-MAX_ROWS:] if r.get('session_id') == snapshot.get('session_id')]
    stops = [number(r.get('lap'), True) for r in rows if r.get('analysis_role') == 'pit' or r.get('id') == service.get('lap_id')]
    stops = [lap for lap in stops if lap is not None]
    if completed is None:
        answers['stint'] = 'No lap completed yet.'
    elif not completed:
        answers['stint'] = 'No race lap completed yet.'
    elif stops:
        laps = completed-max(stops)
        answers['stint'] = f'{laps:g} lap{"" if laps == 1 else "s"} since your last stop.'
    else:
        answers['stint'] = f'{completed:g} lap{"" if completed == 1 else "s"} since the start.'
    return answers


def _rank(snapshot, live):
    value = number((snapshot.get('instruments') or {}).get('race_position'), True)
    return int(value) if live and value is not None and value == int(value) else None


def _position(snapshot, live):
    rank = _rank(snapshot, live)
    return f'P{rank}' if rank is not None else None


def _to_go(total, completed, milestones_only=False):
    """Laps left in a race with a declared length, counted from the completed laps."""
    left = int(total-completed)
    if left < 1 or (milestones_only and left not in TO_GO_MARKS):
        return None
    return 'final lap' if left == 1 else f'{left} to go'


def _finish_text(position, best_s):
    text = f'finished {position}' if position else 'finished'
    return text+(f', best lap: {_lap_clock(best_s)}' if best_s is not None else '.')


def _mandatory(snapshot, setup, service):
    """What the declared mandatory rules still require: each pit lane pass is one stop
    (the box standstill or a refuel marks it), compounds count once declared as fitted."""
    rows = [r for r in (snapshot.get('laps') or [])[-MAX_ROWS:] if r.get('session_id') == snapshot.get('session_id')]
    current = snapshot.get('current') or {}
    pits = {r.get('id') for r in rows if 'pit' in (r.get('lap_role'), r.get('analysis_role')) and not r.get('non_race')}
    if 'pit' in (current.get('lap_role'), current.get('analysis_role')):
        pits.add(current.get('id'))
    required = setup.get('mandatory_stops')
    left = max(0, required-len(pits)) if required is not None else None
    fitted = {setup.get('starting_compound'), current.get('compound'),
              *(service.get('fitted') or [service.get('compound')])}
    missing = [c for c in setup.get('mandatory_compounds') or [] if c not in fitted]
    parts = []
    if left:
        parts.append(f'{left} mandatory stop{"" if left == 1 else "s"} left')
    if missing:
        names = [COMPOUND_NAMES[c] for c in missing]
        parts.append((names[0] if len(names) == 1 else ', '.join(names[:-1])+' and '+names[-1])+' still required')
    return dict(stops_done=len(pits), stops_left=left, compounds_missing=missing, text=', '.join(parts) or None)


def _radio_call(row, usable, fastest, position, to_go=None, total=None, fuel=None, short=False, running=None,
                mandatory=None):
    """The one status line, for the lap update and the short status alike: the lap now
    running, the position, the lap just done, laps left and fuel."""
    parts = []
    done = number(row.get('lap'), True) if row else None
    lap = done+1 if done is not None else number(running, True)
    if lap is not None and (total is None or lap <= total):
        parts.append(f'Lap {lap:g}')
    if position:
        parts.append(position)
    if row:
        parts.append(f"last lap was a {_clock(row['time_s'])}")
        if fastest:
            parts.append('your fastest lap')
    if to_go:
        parts.append(to_go)
    if usable is not None:
        parts.append(f'fuel for {usable:.1f} laps')
    elif fuel is not None:
        parts.append(f'fuel {fuel:.1f} litres')
    if short:
        parts.append('fuel is short of the finish')
    if mandatory:
        parts.append(mandatory)
    if not parts:
        return None
    text = ', '.join(parts)
    return text[0].upper()+text[1:]+'.'


def _lap_text(row, usable, fastest, position, to_go=None, total=None, mandatory=None):
    return _radio_call(row, usable, fastest, position, to_go, total, mandatory=mandatory)


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
        all_rows = [r for r in (snapshot.get('laps') or [])[-MAX_ROWS:]
                    if r.get('session_id') == snapshot.get('session_id') and not r.get('non_race')]
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


def _race_laps(snapshot, skip):
    """The snapshot counted in race laps: a declared non-race opening lap becomes lap 0
    and is marked non_race, every later lap of the session moves down by one."""
    if not skip:
        return snapshot
    sid = snapshot.get('session_id')

    def shift(row):
        lap = number(row.get('lap'), True) if isinstance(row, dict) else None
        if lap is None or row.get('session_id') != sid:
            return row
        return dict(row, lap=lap-skip, non_race=lap <= skip)
    return dict(snapshot, laps=[shift(r) for r in snapshot.get('laps') or []], current=shift(snapshot.get('current')))


def assess(snapshot, session=None, limits=None):
    """Return allowlisted facts/English text for one immutable shared snapshot."""
    limits = settings(limits)
    session = session or {}
    stale_declarations = bool(session.get('setup') or session.get('service')) and (
        binding(snapshot) is None or session.get('binding') != binding(snapshot))
    if stale_declarations:
        session = {}
    setup, service = session.get('setup') or {}, session.get('service') or {}
    kind = setup.get('session')
    # Practice and qualifying have no race distance: no laps to go, no start or finish report.
    raced = kind not in ('practice', 'qualifying')
    race_setup = setup if raced else {k: v for k, v in setup.items() if k not in ('laps', 'duration_min')}
    snapshot = _race_laps(snapshot, 1 if raced and setup.get('opening_lap') else 0)
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
    finish = _finish(snapshot, race_setup, rows, consumption, usable)
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
    lap = _latest_lap(snapshot)
    fastest = lap is not None and _is_fastest(lap, snapshot)
    # In qualifying GT7 reports the qualifying rank in the race position field.
    # Practice calls leave the position out; asked directly, it is named in every session.
    asked_position = _position(snapshot, live)
    position = asked_position if kind != 'practice' else None
    total = number(race_setup.get('laps'), True)
    mandatory = _mandatory(snapshot, setup, service) if raced else dict(stops_done=None, stops_left=None,
                                                                         compounds_missing=[], text=None)
    completed = 0 if lap and lap.get('non_race') else number(lap.get('lap'), True) if lap else None
    race_laps = total is not None and completed is not None
    status_go = _to_go(total, completed) if race_laps and live else None
    status_text = (_radio_call(lap, usable, fastest, position, status_go, total, fuel,
                               finish['finish'] in ('short', 'estimated_short'), (snapshot.get('current') or {}).get('lap'),
                               mandatory['text'])
                   if live else None) or 'Live telemetry is unavailable.'
    eligible = [r for r in (snapshot.get('laps') or [])[-MAX_ROWS:]
            if r.get('session_id') == snapshot.get('session_id') and _counts_for_fastest(r)]
    best=min(eligible,key=lambda r:r['time_s'],default=None)
    last=eligible[-1] if eligible else None
    lap_facts={}
    for label,row in (('fastest',best),('last',last)):
        lap_facts[label+'_s']=row['time_s'] if live and row else None
        lap_facts[label+'_lap']=number(row.get('lap'),True) if live and row else None
    answers = dict(fuel=fuel_text, pace=pace_text, tyres=tyre_text, status=status_text)
    lap_answers=dict(fastest_lap=_lap_answer(best,True,live),last_lap=_lap_answer(last,False,live))
    service_lap = next((number(r.get('lap'), True) for r in (snapshot.get('laps') or [])[-MAX_ROWS:]
                        if service and r.get('id') == service.get('lap_id')), None)
    tyre_age = (completed-service_lap if service_lap is not None and completed is not None
                else number(tyres['age_laps'], True) if not service else None)
    quick_answers = _quick_answers(snapshot, live, asked_position, total, completed, usable, consumption,
                                   compound, tyre_age, eligible if live else [], service, kind)
    return dict(version=1, binding=binding(snapshot), live=live, fuel=fuel_result, pace=pace,
                tyres=tyres, laps=lap_facts, uncertainties=uncertainty, limits=limits, latest_lap_id=lap['id'] if lap else None,
                position=position, rank=_rank(snapshot, live) if position else None, race_laps=total, race_finished=race_laps and completed >= total, completed_laps=completed,
                finish_text=_finish_text(position, best['time_s'] if best else None),
                lap_text=_lap_text(lap, usable, fastest, position, _to_go(total, completed, True) if race_laps else None, total,
                                   mandatory['text']) if lap else None, session=kind, mandatory=mandatory,
                answers=answers,lap_answers=lap_answers,quick_answers=quick_answers)

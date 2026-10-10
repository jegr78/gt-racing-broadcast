#!/usr/bin/env python3
"""Reviewed world-coordinate markers, shared overrides and larger-sector analysis."""
import copy
import hashlib
import json
import math
import os
import re

import gt7_context
import gt7_laps

FORMAT = 'racecast-track-definition'
STORE_FORMAT = 'racecast-track-definition-override'
MAX_CORNERS = 1024
MAX_BOUNDARIES = 256
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ValueError('invalid track definition identifier')
    return value


def _text(value, limit=2000):
    return gt7_context._text(value, limit)


def anchor(value):
    value = copy.deepcopy(gt7_context._object(value))
    gt7_context._keys(value, {'x', 'z'})
    if set(value) != {'x', 'z'} or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                      or not math.isfinite(v) or abs(v) > 100000 for v in value.values()):
        raise ValueError('track anchors require finite world X/Z coordinates')
    return value


def validate_definition(value):
    value = copy.deepcopy(gt7_context._object(value))
    gt7_context._bounded(value)
    gt7_context._keys(value, {'format', 'version', 'layout_id', 'reverse', 'reviewed', 'numbering_scheme',
                             'provenance', 'corners', 'variants', 'default_variant', 'nominal_length_m',
                             'catalogue_turns', 'combinations'})
    if value.get('format') != FORMAT:
        raise ValueError('unsupported track definition format')
    gt7_context._number(value.get('version'), 'definition version', True)
    token(value.get('layout_id'))
    for key in ('reverse', 'reviewed'):
        if not isinstance(value.get(key), bool):
            raise ValueError(key+' must be a boolean')
    _text(value.get('numbering_scheme', ''), 200)
    for item in gt7_context._list(value.get('provenance', [])):
        _text(item)
    if value.get('nominal_length_m') is not None:
        gt7_context._number(value['nominal_length_m'], 'nominal length')
    if value.get('catalogue_turns') is not None:
        gt7_context._number(value['catalogue_turns'], 'catalogue turns', True)
    corners = gt7_context._list(value.get('corners', []))
    if len(corners) > MAX_CORNERS:
        raise ValueError('too many corner markers')
    ids, numbers = set(), set()
    for corner in corners:
        gt7_context._object(corner)
        gt7_context._keys(corner, {'id', 'number', 'name', 'direction', 'entry', 'apex', 'exit'})
        identity = token(corner.get('id'))
        number = gt7_context._number(corner.get('number'), 'corner number', True)
        if identity in ids or number in numbers or number < 1:
            raise ValueError('corner identities and positive numbers must be unique')
        ids.add(identity)
        numbers.add(number)
        _text(corner.get('name', ''), 200)
        if corner.get('direction') not in {'left', 'right', 'unknown'}:
            raise ValueError('unsupported corner direction')
        for key in ('entry', 'apex', 'exit'):
            corner[key] = anchor(corner.get(key))
    variants = gt7_context._list(value.get('variants', []))
    variant_ids = set()
    for variant in variants:
        gt7_context._object(variant)
        gt7_context._keys(variant, {'id', 'name', 'kind', 'reviewed', 'boundaries', 'sector_names', 'evidence'})
        identity = token(variant.get('id'))
        if identity in variant_ids:
            raise ValueError('sector variant identities must be unique')
        variant_ids.add(identity)
        _text(variant.get('name'), 200)
        if variant.get('kind') not in {'coaching', 'game', 'provisional'} or not isinstance(variant.get('reviewed'), bool):
            raise ValueError('invalid sector kind or review status')
        if variant['kind'] == 'provisional' and variant['reviewed']:
            raise ValueError('provisional sectors cannot be reviewed')
        if variant['kind'] == 'game' and variant['reviewed'] and not variant.get('evidence'):
            raise ValueError('reviewed game splits require verification evidence')
        _text(variant.get('evidence', ''))
        boundaries = gt7_context._list(variant.get('boundaries'))
        if not 2 <= len(boundaries) <= MAX_BOUNDARIES:
            raise ValueError('sector variants require 2 to 256 world boundaries')
        variant['boundaries'] = [anchor(p) for p in boundaries]
        names = gt7_context._list(variant.get('sector_names', []))
        if len(names) != len(boundaries)-1:
            raise ValueError('every larger sector needs a name')
        for name in names:
            _text(name, 200)
    if value.get('default_variant') not in variant_ids:
        raise ValueError('default sector variant is unavailable')
    combinations = gt7_context._list(value.get('combinations', []))
    for combination in combinations:
        gt7_context._object(combination)
        gt7_context._keys(combination, {'id', 'name', 'corner_ids'})
        token(combination.get('id'))
        _text(combination.get('name'), 200)
        selected = gt7_context._list(combination.get('corner_ids'))
        if not selected or len(set(selected)) != len(selected) or any(c not in ids for c in selected):
            raise ValueError('corner combination needs distinct existing identities')
    return value


def fingerprint(definition):
    return hashlib.sha256(json.dumps(definition, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


class Store:
    """Machine-wide local snapshots; shipped bases are read-only and never overwritten."""
    def __init__(self, runtime_base, bundled):
        self.root = os.path.join(os.path.abspath(runtime_base), 'gt7-track-definitions')
        self.bundled = os.path.abspath(bundled)

    def path(self, layout, reverse):
        token(layout)
        if not isinstance(reverse, bool):
            raise ValueError('track direction must be a boolean')
        return os.path.join(self.root, layout+('-reverse' if reverse else '-forward')+'.json')

    def read(self, layout, reverse):
        path = self.path(layout, reverse)
        supplied = gt7_context._load(os.path.join(self.bundled, os.path.basename(path)))
        if supplied is not None:
            supplied = validate_definition(supplied)
            if supplied['layout_id'] != layout or supplied['reverse'] != reverse:
                raise ValueError('supplied track definition has a different layout or direction')
        doc = gt7_context._load(path)
        if doc is None:
            return {'definition': supplied, 'revision': 0, 'source': 'supplied' if supplied else 'unsupported',
                    'base': copy.deepcopy(supplied)}
        gt7_context._object(doc)
        if doc.get('format') != STORE_FORMAT or doc.get('version') != 1:
            raise ValueError('unsupported track override format')
        revision = gt7_context._number(doc.get('revision'), 'override revision', True)
        definition = validate_definition(doc.get('definition'))
        if definition['layout_id'] != layout or definition['reverse'] != reverse:
            raise ValueError('track override has a different layout or direction')
        return {'definition': definition, 'revision': revision, 'source': 'override', 'base': supplied}

    def save(self, definition, expected):
        definition = validate_definition(definition)
        expected = gt7_context._number(expected, 'expected revision', True)
        path = self.path(definition['layout_id'], definition['reverse'])
        with gt7_context._LOCK, gt7_context._file_lock(path):
            current = self.read(definition['layout_id'], definition['reverse'])
            if current['revision'] != expected:
                raise gt7_context.Conflict('track definition changed; reload before applying')
            revision = expected+1
            base_version = current['base']['version'] if current['base'] else 0
            definition['version'] = max(revision+base_version, definition['version'])
            gt7_context._atomic(path, {'format': STORE_FORMAT, 'version': 1,
                                      'revision': revision, 'definition': definition})
            return self.read(definition['layout_id'], definition['reverse'])


def project_definition(definition, tracks):
    definition = validate_definition(definition)
    layout = definition['layout_id']
    identity = tracks.name(layout)
    if not identity or identity.get('reverse') != definition['reverse']:
        raise ValueError('track reference direction does not match the definition')
    length = tracks.line_length(layout)
    if length is None or not math.isfinite(length) or not 0 < length <= gt7_laps.MAX_TRACE_M:
        raise ValueError('track reference geometry is unavailable')
    result = copy.deepcopy(definition)
    result['reference_length_m'] = length
    result['fingerprint'] = fingerprint(definition)
    def stations(points):
        projected = tracks.project([(p['x'], p['z']) for p in points], layout)
        if not projected or len(projected) != len(points) or any(s is None or not math.isfinite(s) for s in projected):
            raise ValueError('world anchors cannot be projected onto this layout')
        return projected
    for corner in result['corners']:
        entry, apex, exit_ = stations([corner[k] for k in ('entry', 'apex', 'exit')])
        if not 0 <= entry <= apex <= exit_ <= length or exit_-entry < 1:
            raise ValueError('corner anchors must follow entry, apex and exit in driving order')
        corner.update(entry_m=entry, apex_m=apex, exit_m=exit_)
    for variant in result['variants']:
        ss = stations(variant['boundaries'])
        if min(ss[0], length-ss[0]) > gt7_laps.STEP_M*10 or min(ss[-1], length-ss[-1]) > gt7_laps.STEP_M*10:
            raise ValueError('sector endpoints must match the reference start/finish')
        bounds = [0.0]+ss[1:-1]+[round(length, 1)]  # same closing station as indexed complete traces
        if any(b-a < 1 for a, b in zip(bounds, bounds[1:], strict=False)):
            raise ValueError('sector boundaries must follow driving order with nonzero lengths')
        variant['bounds_m'] = bounds
    return result


def _eligible(lap):
    return bool(gt7_laps.pace_eligible(lap) and lap.get('comparison_eligible', True)
                and lap.get('capture_complete') and lap.get('trace_complete') and lap.get('time_valid')
                and lap.get('analysis_role') not in {'first', 'pit', 'warmup', 'partial'})


def sector_times(lap, variant):
    times = [gt7_laps._time_at(lap.get('trace', []), d) for d in variant['bounds_m']]
    return [None if a is None or b is None or b < a else b-a
            for a, b in zip(times, times[1:], strict=False)]


def theoretical(laps, definition, variant_id):
    variant = next((v for v in definition['variants'] if v['id'] == variant_id), None)
    if variant is None:
        raise ValueError('selected larger-sector variant is unavailable')
    groups = []
    for lap in laps:
        if not _eligible(lap):
            continue
        stored = lap.get('larger_sectors') or {}
        times = stored.get('times_s') if (stored.get('variant_id') == variant_id
                 and lap.get('definition_fingerprint') == definition['fingerprint']
                 and stored.get('bounds_m') == variant['bounds_m']) else sector_times(lap, variant)
        if (not isinstance(times, list) or len(times) != len(variant['sector_names'])
                or any(t is None or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0 for t in times)):
            continue
        for group in groups:
            if all(lap.get('track_id') == other.get('track_id') and lap.get('car_id') == other.get('car_id')
                   and gt7_context.compatible(lap, other) and gt7_context.compatible(other, lap) for other, _t in group):
                group.append((lap, times))
                break
        else:
            groups.append([(lap, times)])
    results = []
    for group in groups:
        best = []
        confirmed = definition['reviewed'] and variant['reviewed'] and variant['kind'] != 'provisional'
        for sector, name in enumerate(variant['sector_names']):
            source, times = min(group, key=lambda item: item[1][sector])
            confirmed = confirmed and bool(source.get('context_confirmed') and source.get('compound')
                                           and not source.get('track_definition_warnings'))
            best.append({'name': name, 'time_s': times[sector],
                         'source': {k: source.get(k) for k in ('rec', 'session', 'lap', 'compound')},
                         'start_m': variant['bounds_m'][sector], 'end_m': variant['bounds_m'][sector+1]})
        results.append({'time_s': sum(s['time_s'] for s in best), 'best_sectors': best,
                        'confirmed': bool(confirmed), 'compound': group[0][0].get('compound')})
    chosen = min(results, key=lambda result: (not result['confirmed'], result['time_s'])) if results else {}
    return dict(chosen, confirmed=chosen.get('confirmed', False), groups=results,
                variant_id=variant_id, variant_name=variant['name'], kind=variant['kind'],
                definition_version=definition['version'], definition_fingerprint=definition['fingerprint'],
                interpretation='Ideal sector composition; entry-speed dependencies remain')


def corner_metrics(lap, definition):
    trace = lap.get('trace', [])
    out = []
    for corner in definition['corners']:
        start, end = corner['entry_m'], corner['exit_m']
        part = [p for p in trace if start <= p['d'] <= end]
        times = [gt7_laps._time_at(trace, d) for d in (start, end)]
        speeds = []
        for d in (start, corner['apex_m'], end):
            i = next((i for i, p in enumerate(trace) if p['d'] >= d), None)
            speeds.append(trace[i].get('speed_kmh') if i is not None else None)
        out.append({'id': corner['id'], 'number': corner['number'], 'name': corner.get('name', ''),
                    'direction': corner['direction'], 'entry_m': start, 'apex_m': corner['apex_m'],
                    'exit_m': end, 'time_s': None if None in times else times[1]-times[0],
                    'entry_speed_kmh': speeds[0], 'anchor_speed_kmh': speeds[1], 'exit_speed_kmh': speeds[2],
                    'min_speed_kmh': min((p['speed_kmh'] for p in part), default=None),
                    'max_brake_pct': max((p['brake'] for p in part), default=None)})
    return out


def proposals(trace, layout, reverse):
    """Geometry-only candidates; identities and review are decided by explicit editing."""
    def at(d):
        return min(trace, key=lambda p: abs(p['d']-d))
    if len(trace) < 3 or trace[-1]['d'] <= 0:
        raise ValueError('complete track geometry is unavailable for proposals')
    length = trace[-1]['d']
    peaks = []
    for i in range(4, len(trace)-4):
        a, b, c = trace[i-4], trace[i], trace[i+4]
        if b['d'] <= 30 or b['d'] >= length-30:
            continue  # boundary-crossing bends need explicit authored review
        u, v = (b['x']-a['x'], b['z']-a['z']), (c['x']-b['x'], c['z']-b['z'])
        cross, dot = u[0]*v[1]-u[1]*v[0], u[0]*v[0]+u[1]*v[1]
        angle = abs(math.atan2(cross, dot))
        if angle < math.radians(12):
            continue
        if peaks and b['d']-peaks[-1][0]['d'] < 75:
            if angle > peaks[-1][1]:
                peaks[-1] = (b, angle, cross)
        else:
            peaks.append((b, angle, cross))
    corners = []
    for n, (point, _angle, cross) in enumerate(peaks[:MAX_CORNERS], 1):
        corners.append({'id': 'proposal-'+str(n), 'number': n, 'name': '',
                        'direction': 'left' if cross < 0 else 'right',
                        **{key: {axis: at(max(0, min(length, point['d']+delta)))[axis] for axis in ('x', 'z')}
                           for key, delta in [('entry', -30), ('apex', 0), ('exit', 30)]}})
    bounds = [{axis: at(d)[axis] for axis in ('x', 'z')} for d in (0, length/3, length*2/3, 0)]
    return validate_definition({'format': FORMAT, 'version': 1, 'layout_id': layout, 'reverse': reverse,
                                'reviewed': False, 'numbering_scheme': 'Unreviewed geometry candidates',
                                'provenance': ['Geometry candidates require manual review; no official numbering inferred'],
                                'corners': corners, 'variants': [{'id': 'provisional-3', 'name': 'Provisional thirds',
                                  'kind': 'provisional', 'reviewed': False, 'boundaries': bounds,
                                  'sector_names': ['Section 1', 'Section 2', 'Section 3']}],
                                'default_variant': 'provisional-3'})


def annotate_index(index, context, store, tracks, full_index=None):
    """Current analysis uses current definitions; saved output retains the applied snapshot."""
    result = dict(index)
    snapshots = {}
    rows = index.get('laps', [])+([index['open_lap']] if index.get('open_lap') else [])
    full_rows = (full_index or index).get('laps', [])+(
        [(full_index or index)['open_lap']] if (full_index or index).get('open_lap') else [])
    traces = {(r['session'], r['lap']): r for r in full_rows}
    track_context = context.get('track_definition') or {}
    selections = track_context.get('selections', {}) if isinstance(track_context, dict) else {}
    if not isinstance(selections, dict):
        selections = {}
    definitions, errors = {}, {}
    for row in rows:
        layout = row.get('track_id')
        identity = tracks.name(layout) if layout else None
        if not identity:
            continue
        key = (layout, identity['reverse'])
        if key in definitions or key in errors:
            continue
        try:
            saved = store.read(*key)
            definition = saved['definition']
            if definition is None:
                source = max((r for r in full_rows if r.get('track_id') == layout and r.get('trace')),
                             key=lambda r: r.get('length_m') or 0, default=None)
                if source is None:
                    continue
                definition = proposals(source['trace'], *key)
            projected = project_definition(definition, tracks)
            projected['storage_source'] = saved['source']
            definitions[key] = projected
            snapshots[projected['fingerprint']] = definition
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors[key] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    annotated = []
    for row in rows:
        output = dict(row)
        output.update(track_definition_warnings=[], larger_sectors=None, corner_metrics=[], combination_metrics=[])
        layout = row.get('track_id')
        identity = tracks.name(layout) if layout else None
        key = (layout, identity['reverse']) if identity else None
        projected = definitions.get(key)
        if projected is not None:
            selected = selections.get(str(row['session']))
            variant_id = next((v['id'] for v in projected['variants']
                               if projected['reviewed'] and v['reviewed'] and v['kind'] == 'game'),
                              projected['default_variant'])
            if selected:
                if (not isinstance(selected, dict) or selected.get('layout_id') != layout
                        or selected.get('reverse') != identity['reverse']
                        or selected.get('variant_id') not in {v['id'] for v in projected['variants']}):
                    output['track_definition_warnings'].append('Selected sector definition no longer matches this layout/variant')
                else:
                    variant_id = selected['variant_id']
            variant = next(v for v in projected['variants'] if v['id'] == variant_id)
            trace_source = dict(row, trace=traces.get((row['session'], row['lap']), {}).get('trace', []))
            output.update(definition_fingerprint=projected['fingerprint'], sector_variant_id=variant_id,
                          track_definition=projected,
                          larger_sectors={'variant_id': variant_id, 'name': variant['name'],
                                          'kind': variant['kind'], 'reviewed': variant['reviewed'] and projected['reviewed']
                                          and not output['track_definition_warnings'],
                                          'bounds_m': variant['bounds_m'], 'names': variant['sector_names'],
                                          'times_s': sector_times(trace_source, variant)},
                          corner_metrics=corner_metrics(trace_source, projected),
                          combination_metrics=combination_metrics(trace_source, projected))
        elif key in errors:
            output['track_definition_warnings'].append('Track definition unavailable: '+errors[key])
        else:
            output['track_definition_warnings'].append('No confirmed marker or sector definition for this layout')
        annotated.append(output)
    result['laps'] = annotated[:len(index.get('laps', []))]
    if index.get('open_lap'):
        result['open_lap'] = annotated[-1]
    result['track_definition_snapshots'] = snapshots
    return result


def combination_metrics(lap, definition):
    out = []
    trace = lap.get('trace', [])
    for combination in definition.get('combinations', []):
        selected = [c for c in definition['corners'] if c['id'] in combination['corner_ids']]
        if not selected:
            continue
        start, end = min(c['entry_m'] for c in selected), max(c['exit_m'] for c in selected)
        times = [gt7_laps._time_at(trace, d) for d in (start, end)]
        out.append({'id': combination['id'], 'name': combination['name'],
                    'numbers': [c['number'] for c in sorted(selected, key=lambda c: c['apex_m'])],
                    'start_m': start, 'end_m': end, 'time_s': None if None in times else times[1]-times[0]})
    return out

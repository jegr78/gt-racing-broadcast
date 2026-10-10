#!/usr/bin/env python3
"""Authored world anchors, shared overrides and eligible larger-sector composition."""
import copy
import math
import os
import sys
import tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src', 'scripts'))
import gt7_track_definitions as td
import test_gt7_laps as fixture


def definition():
    radius = fixture.R
    def anchor(d):
        angle = d / radius
        return {'x': radius*math.cos(angle), 'z': radius*math.sin(angle)}
    return {'format': td.FORMAT, 'version': 1, 'layout_id': 'ring01', 'reverse': False,
            'reviewed': True, 'numbering_scheme': 'Authored test corners', 'provenance': ['Own synthetic geometry'],
            'corners': [{'id': 'turn-a', 'number': 1, 'name': 'First bend', 'direction': 'right',
                         'entry': anchor(100), 'apex': anchor(150), 'exit': anchor(200)}],
            'variants': [{'id': 'coach-3', 'name': 'Coaching three', 'kind': 'coaching',
                          'reviewed': True, 'boundaries': [anchor(0), anchor(300), anchor(700), anchor(0)],
                          'sector_names': ['Opening', 'Middle', 'Closing']}],
            'default_variant': 'coach-3'}


def t_world_anchors_project_consistently_without_catalogue_length_scaling():
    doc = definition()
    projected = td.project_definition(doc, fixture.FakeTracks())
    assert projected['corners'][0]['number'] == 1
    assert abs(projected['corners'][0]['apex_m']-150) < 1e-6
    assert projected['variants'][0]['bounds_m'] == [0, 300, 700, 1000]
    altered = copy.deepcopy(doc)
    altered['nominal_length_m'] = 1234
    assert td.project_definition(altered, fixture.FakeTracks())['variants'][0]['bounds_m'] == [0, 300, 700, 1000]
    assert doc['corners'][0]['id'] == 'turn-a', 'projection never renumbers marker identities'


def t_shared_overrides_are_revisioned_and_preserve_supplied_data():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bundle:
        import json
        supplied = os.path.join(bundle, 'ring01-forward.json')
        with open(supplied, 'w', encoding='utf-8') as f:
            json.dump(definition(), f)
        a, b = td.Store(root, bundle), td.Store(root, bundle)
        first = a.read('ring01', False)
        assert first['source'] == 'supplied' and first['revision'] == 0
        changed = copy.deepcopy(first['definition'])
        changed['corners'][0]['number'] = 7
        saved = a.save(changed, 0)
        assert b.read('ring01', False)['definition']['corners'][0]['number'] == 7
        assert saved['revision'] == 1 and saved['source'] == 'override'
        try:
            b.save(definition(), 0)
        except ValueError as exc:
            assert 'changed' in str(exc)
        else:
            raise AssertionError('stale shared edits must preserve the applied definition')
        with open(supplied, encoding='utf-8') as f:
            assert json.load(f)['corners'][0]['number'] == 1


def lap(n, secs, compound='RM', strategy=None, confirmed=True):
    return {'rec': 'R', 'session': 1, 'lap': n, 'track_id': 'ring01', 'car_id': 485,
            'trace': fixture._trace(*secs, sector=250), 'length_m': 1000,
            'capture_complete': True, 'trace_complete': True, 'time_valid': True,
            'pace_eligible': True, 'comparison_eligible': True, 'analysis_role': 'regular',
            'compound': compound, 'context_confirmed': confirmed, 'strategy': strategy or {},
            'context_settings': {}, 'settings_confirmed': confirmed}


def t_sector_composition_has_source_laps_and_cannot_mix_known_strategies():
    projected = td.project_definition(definition(), fixture.FakeTracks())
    variant = projected['variants'][0]
    a, b = lap(2, (4, 4, 4, 4)), lap(3, (3, 5, 3, 5))
    result = td.theoretical([a, b], projected, variant['id'])
    assert result['confirmed'] and len(result['best_sectors']) == 3
    assert all(s['source']['lap'] in (2, 3) for s in result['best_sectors'])
    assert abs(sum(s['time_s'] for s in result['best_sectors'])-result['time_s']) < 1e-6
    unknown = lap(4, (5, 5, 5, 5))
    a['strategy'], b['strategy'] = {'fuel_map': 1}, {'fuel_map': 2}
    groups = td.theoretical([unknown, a, b], projected, variant['id'])['groups']
    for group in groups:
        sources = [s['source']['lap'] for s in group['best_sectors']]
        assert not (2 in sources and 3 in sources), 'unknown settings cannot bridge contradictory known strategies'
    partial = copy.deepcopy(projected)
    partial['variants'][0]['reviewed'] = False
    assert not td.theoretical([a], partial, variant['id'])['confirmed']
    assert not td.theoretical([lap(5, (4, 4, 4, 4), confirmed=False)], projected, variant['id'])['confirmed']


def t_context_cannot_admit_a_numerically_ineligible_source_sector():
    projected = td.project_definition(definition(), fixture.FakeTracks())
    bad = lap(8, (1, 1, 1, 1))
    bad['pace_eligible'] = False
    result = td.theoretical([bad], projected, 'coach-3')
    assert not result.get('time_s') and not result['confirmed'], 'manual eligibility cannot override capture quality'


def t_invalid_geometry_identifiers_and_review_claims_preserve_the_applied_override():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bundle:
        store = td.Store(root, bundle)
        store.save(definition(), 0)
        changes = [
            lambda d: d['corners'][0]['apex'].update(x=float('nan')),
            lambda d: d.update(layout_id='../elsewhere'),
            lambda d: d['corners'].append(copy.deepcopy(d['corners'][0])),
            lambda d: d['variants'][0].update(kind='provisional'),
            lambda d: d['variants'][0].update(kind='game'),
            lambda d: d.update(reverse='false')]
        for change in changes:
            altered = definition()
            change(altered)
            try:
                store.save(altered, 1)
            except ValueError:
                pass  # invalid drafts cannot replace the applied shared definition
            else:
                raise AssertionError('invalid track definition must be refused')
            assert store.read('ring01', False)['revision'] == 1
        backwards = definition()
        backwards['variants'][0]['boundaries'][1:3] = reversed(backwards['variants'][0]['boundaries'][1:3])
        try:
            td.project_definition(backwards, fixture.FakeTracks())
        except ValueError as exc:
            assert 'driving order' in str(exc)
        else:
            raise AssertionError('reversed boundaries cannot fabricate positive source sectors')


def t_reverse_definitions_cannot_project_as_confirmed_forward_geometry():
    altered = definition()
    altered['reverse'] = True
    try:
        td.project_definition(altered, fixture.FakeTracks())
    except ValueError as exc:
        assert 'direction' in str(exc)
    else:
        raise AssertionError('forward geometry cannot confirm a reverse definition')


def t_supplied_grand_valley_uses_reviewed_publisher_numbering_and_coaching_sections():
    with tempfile.TemporaryDirectory() as root:
        store = td.Store(root, os.path.join(ROOT, 'src', 'assets', 'gt7', 'track-definitions'))
        item = store.read('c2fd94', False)
        doc = item['definition']
        assert doc and doc['reviewed'], 'Grand Valley needs a supplied reviewed definition'
        assert [c['number'] for c in doc['corners']] == list(range(1, 20))
        assert doc['catalogue_turns'] == 18 and doc['nominal_length_m'] == 5099
        assert doc['corners'][0]['direction'] == 'left' and doc['corners'][-1]['direction'] == 'right'
        assert len(doc['variants'][0]['boundaries']) == 4
        assert doc['variants'][0]['kind'] == 'coaching' and doc['variants'][0]['reviewed']
        assert any('gran-turismo.com' in source for source in doc['provenance'])
        assert store.read('c2fd94', True)['definition'] is None, 'forward anchors must not imply reverse-layout coverage'


def t_sector_finish_uses_the_same_rounded_station_as_completed_traces():
    class PreciseLength(fixture.FakeTracks):
        def line_length(self, oid):
            return 1000.04
    projected = td.project_definition(definition(), PreciseLength())
    times = td.sector_times(lap(2, (4, 4, 4, 4)), projected['variants'][0])
    assert all(t is not None for t in times), 'full source traces must cover the larger-sector finish'
    assert abs(sum(times)-16) < 1e-6


def t_analysis_joins_definition_snapshots_without_mutating_raw_indexes():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bundle:
        store = td.Store(root, bundle)
        store.save(definition(), 0)
        raw = {'rec': 'R', 'laps': [lap(2, (4, 4, 4, 4))], 'open_lap': None}
        before = copy.deepcopy(raw)
        analysed = td.annotate_index(raw, {}, store, fixture.FakeTracks())
        assert raw == before, 'derived sector analysis cannot mutate a cached raw index'
        row = analysed['laps'][0]
        assert len(row['larger_sectors']['times_s']) == 3
        assert row['sector_variant_id'] == 'coach-3'
        assert len(row['corner_metrics']) == 1
        assert analysed['track_definition_snapshots'][row['definition_fingerprint']]['corners'][0]['id'] == 'turn-a'
        context = {'track_definition': {'selections': {'1': {'layout_id': 'elsewhere', 'reverse': False, 'variant_id': 'unknown'}}}}
        conflict = td.annotate_index(raw, context, store, fixture.FakeTracks())['laps'][0]
        assert conflict['track_definition_warnings'], 'a stale selection must remain visible'
        assert not td.theoretical([conflict], conflict['track_definition'], conflict['sector_variant_id'])['confirmed'], 'a conflicting selection cannot produce a confirmed sector best'
        empty = td.annotate_index(raw, {'track_definition': None}, store, fixture.FakeTracks())
        assert empty['laps'][0]['larger_sectors'], 'clearing optional selection metadata must preserve analysis'


def t_verified_game_divisions_are_preferred_without_overriding_a_session_choice():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bundle:
        store = td.Store(root, bundle)
        doc = definition()
        game = copy.deepcopy(doc['variants'][0])
        game.update(id='game-3', name='Verified game splits', kind='game', evidence='Own in-game split observation')
        doc['variants'].append(game)
        store.save(doc, 0)
        raw = {'rec': 'R', 'laps': [lap(2, (4, 4, 4, 4))], 'open_lap': None}
        assert td.annotate_index(raw, {}, store, fixture.FakeTracks())['laps'][0]['sector_variant_id'] == 'game-3', \
            'verified game sectors should be the default when available'
        context = {'track_definition': {'selections': {'1': {'layout_id': 'ring01', 'reverse': False, 'variant_id': 'coach-3'}}}}
        assert td.annotate_index(raw, context, store, fixture.FakeTracks())['laps'][0]['sector_variant_id'] == 'coach-3', \
            'an explicit session selection must retain its coaching division'


def t_csv_export_freezes_applied_sector_and_definition_snapshots():
    import csv
    import json
    import gt7_recording
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as bundle:
        path = fixture.write_circle_recording(root)
        idx = fixture._index(path)
        store = td.Store(root, bundle)
        store.save(definition(), 0)
        analysed = td.annotate_index(idx, {}, store, fixture.FakeTracks())
        output = os.path.join(root, 'export')
        gt7_recording.export_csv(path, output, track_analysis=analysed)
        with open(os.path.join(output, 'track-definitions.json'), encoding='utf-8') as f:
            snapshot = json.load(f)
        with open(os.path.join(output, 'sectors.csv'), newline='', encoding='utf-8') as f:
            sectors = list(csv.DictReader(f))
        assert snapshot['definitions'] and any(s['variant_id'] == 'coach-3' for s in sectors)
        changed = definition()
        changed['corners'][0]['number'] = 9
        store.save(changed, 1)
        with open(os.path.join(output, 'track-definitions.json'), encoding='utf-8') as f:
            assert json.load(f) == snapshot, 'later map edits cannot rewrite an exported definition'


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('t_') and callable(fn):
            fn(); print('ok', name)
    print('ALL PASS')

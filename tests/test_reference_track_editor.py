#!/usr/bin/env python3
"""Prepare shared track definitions from downloaded geometry before recording."""
import copy
import math
import os
from unittest import mock

import test_racecast as rc
import test_gt7_tracks as tracks_fixture
import test_gt7_laps as laps_fixture

m = rc.m


def tracks(root):
    path = [(laps_fixture.R*math.cos(i*math.tau/360), laps_fixture.R*math.sin(i*math.tau/360)) for i in range(360)]
    catalog = [{'official_id': oid, 'track': 'Test Ring', 'layout': name, 'reverse': reverse,
                'official_name': name, 'length_m': 1000}
               for oid, name, reverse in [('ring01', 'Forward', False), ('ring-reverse', 'Reverse', True), ('learn01', 'Learned', False)]]
    db = tracks_fixture._db(root, [tracks_fixture._row('ring01', 'Ring', path, reverse={'official_id': 'ring-reverse'})], catalog)
    db.learn('learn01', [(x+5000, z) for x, z in path], 1000)
    return db


def t_reference_trace_preserves_start_and_direction_and_excludes_learned_lines():
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        db = tracks(root)
        forward, reverse = db.reference_trace('ring01'), db.reference_trace('ring-reverse')
        assert forward[0]['d'] == reverse[0]['d'] == 0
        assert forward[-1]['d'] == reverse[-1]['d'] == db.line_length('ring01')
        assert (forward[0]['x'], forward[0]['z']) == (reverse[0]['x'], reverse[0]['z'])
        assert reverse[1]['x'] == forward[-2]['x'] and reverse[1]['z'] == forward[-2]['z']
        for oid, trace in [('ring01', forward), ('ring-reverse', reverse)]:
            assert all(a['d'] < b['d'] for a, b in zip(trace, trace[1:], strict=False))
            projected = db.project([(p['x'], p['z']) for p in trace[:-1]], oid)
            assert all(abs(got-point['d']) < .001 for got, point in zip(projected, trace, strict=False))
        assert db.reference_trace('learn01') is None and db.reference_trace('missing') is None


def t_reference_editor_applies_before_any_recording_and_later_analysis_uses_it():
    with rc._telemetry_sandbox(fake_dbs=False) as (recdir, _fixture):
        root = os.path.join(os.path.dirname(recdir), 'reference-data')
        os.makedirs(root)
        db = tracks(root)
        with mock.patch.object(m, '_telemetry_dbs', return_value=(db, laps_fixture.FakeCars())):
            available = m.telemetry_definition_layouts_data()
            assert {row['id'] for row in available['tracks']} == {'ring01', 'ring-reverse'}
            assert not os.listdir(recdir), 'fixture must not contain a recording yet'
            read = m.telemetry_track_definition_read_data(layout_id='ring01', reverse=False)
            assert read['ok'] and read['scope'] == 'layout' and not read['definition']['reviewed'], read
            assert 'reference' in ' '.join(read['definition']['provenance']).lower()
            assert read['projection'] and len(read['geometry']) > 100
            draft = copy.deepcopy(read['definition'])
            draft['numbering_scheme'] = 'Reviewed reference proposal'
            draft['reviewed'] = True
            draft['variants'][0].update(kind='coaching', reviewed=True, sector_names=['Opening', 'Middle', 'Closing'])
            payload = dict(scope='layout', layout_id='ring01', reverse=False, profile=read['profile'],
                           source_id=read['source_id'], expected_revision=read['revision'], definition=draft)
            saved = m.telemetry_track_definition_write_data(payload)
            assert saved['ok'] and saved['revision'] == 1 and saved['definition']['reviewed'], saved
            assert m.telemetry_track_definition_write_data(payload)['conflict']
            foreign = dict(payload, expected_revision=1, profile='another-profile')
            assert m.telemetry_track_definition_write_data(foreign)['conflict']
            wrong = dict(payload, expected_revision=1, reverse=True)
            assert not m.telemetry_track_definition_write_data(wrong)['ok']
            path = laps_fixture.write_circle_recording(recdir)
            lap = m.telemetry_lap_data(rc._stem(path), '1', '3')['lap']
            assert lap['track_definition']['numbering_scheme'] == 'Reviewed reference proposal'
            assert lap['larger_sectors']['names'] == ['Opening', 'Middle', 'Closing']
            again = m.telemetry_track_definition_read_data(layout_id='ring01', reverse=False)
            assert again['source'] == 'override' and again['revision'] == 1
            changed = tracks_fixture.gt._Line([(x+5, z) for x, z in db._shipped['ring01']['line'].pts])
            db._shipped['ring01']['line'] = changed
            assert m.telemetry_track_definition_write_data(dict(payload, expected_revision=1))['conflict']
            for oid, reverse in [('learn01', False), ('missing', False), ('ring01', True), ('../ring01', False)]:
                answer = m.telemetry_track_definition_read_data(layout_id=oid, reverse=reverse)
                assert not answer['ok'], (oid, answer)


def t_layout_routes_keep_domain_conflicts_and_reject_foreign_origins():
    import json
    import urllib.request
    import urllib.error
    import test_ui_server as ui
    ctx = ui._ctx()
    ctx['telemetry_definition_layouts'] = lambda: {'ok': True, 'tracks': [{'id': 'ring01', 'reverse': False}]}
    ctx['telemetry_definition_read'] = lambda **scope: {'ok': True, 'scope': scope}
    ctx['telemetry_definition_write'] = lambda payload: {'ok': False, 'conflict': True, 'error': 'reference geometry changed'}
    server, port = ui._serve(ctx)
    try:
        code, body = ui._get(port, '/api/telemetry/definition-layouts')
        assert code == 200 and json.loads(body)['tracks'][0]['id'] == 'ring01', body
        code, body = ui._get(port, '/api/telemetry/definition?layout_id=ring01&reverse=false')
        assert code == 200 and json.loads(body)['scope']['layout_id'] == 'ring01', body
        code, body = ui._post_json(port, '/api/telemetry/definition', {'scope': 'layout'})
        assert code == 409 and json.loads(body)['conflict'] and json.loads(body)['error'] == 'reference geometry changed'
        for route in ('/api/telemetry/definition-layouts', '/api/telemetry/definition?layout_id=ring01&reverse=false'):
            request = urllib.request.Request(f'http://127.0.0.1:{port}'+route, headers={'Origin': 'http://foreign.test'})
            try:
                with ui._urlopen(request) as response:
                    code, body = response.status, response.read()
            except urllib.error.HTTPError as response:
                code, body = response.code, response.read()
            assert code == 403 and 'origin' in json.loads(body)['error'], body
    finally:
        server.shutdown()
        server.server_close()


def t_layout_editor_opens_without_a_lap_and_discards_a_late_profile_answer():
    import json
    import test_ui_server as ui
    out = ui._tm_node("""
$('tm-editor-modal').querySelectorAll=()=>[];tmEditorStatus=()=>{};tmEditorRender=()=>{};
tmState.lapB=null;tmState.profile='alpha';
const first=tmEditorOpen({layout_id:'ring01',reverse:false});await tick();
answer('layout_id=ring01',{ok:true,definition:{numbering_scheme:'proposal'}});await first;
const opened=tmEditor.draft?.numbering_scheme;
const late=tmEditorOpen({layout_id:'ring01',reverse:false});await tick();
tmState.profile='beta';
answer('layout_id=ring01',{ok:true,definition:{numbering_scheme:'wrong profile'}});await late;
console.log(JSON.stringify({opened,draft:tmEditor.draft}));
""")
    if out is not None:
        assert json.loads(out) == {'opened': 'proposal', 'draft': None}, out


def t_layout_list_discards_late_answers_and_shows_missing_data_guidance():
    import json
    import test_ui_server as ui
    out = ui._tm_node("""
$('tm-reference-layouts').open=true;tmState.profile='alpha';
const pending=tmReferenceLayoutsLoad();await tick();tmReferenceLayoutsReset();tmState.profile='beta';
answer('definition-layouts',{ok:true,tracks:[{id:'stale'}]});await pending;
const stale=tmReferenceLayouts.rows.length;
$('tm-reference-layouts').open=true;const empty=tmReferenceLayoutsLoad();await tick();
answer('definition-layouts',{ok:true,tracks:[]});await empty;
console.log(JSON.stringify({stale,disabled:$('tm-reference-edit').disabled,message:$('tm-reference-status').textContent}));
""")
    if out is not None:
        answer = json.loads(out)
        assert answer['stale'] == 0 and answer['disabled'] and 'gt7-data update' in answer['message'], answer


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn()
            print('PASS', name)

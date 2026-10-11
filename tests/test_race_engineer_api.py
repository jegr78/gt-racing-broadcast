#!/usr/bin/env python3
"""Versioned public projections, shared math and signed request-level authorization."""
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import race_engineer_api as api
import race_engineer_live as live
import race_engineer_access as access
import gt7_context
import gt7_comparison
import test_console_gate as gate
from test_race_engineer_live import feed, FakeCars, FakeTracks, write_circle_recording


def wait_ready(fn):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        result = fn()
        if result['state'] != 'pending':
            return result
        time.sleep(.01)
    raise AssertionError('shared work never completed')


def t_thin_snapshot_history_revision_and_private_projection():
    with tempfile.TemporaryDirectory() as d:
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), runtime_base=d, queue_max=4096) as store:
            feed(store, write_circle_recording(d))
            service = api.Service(store)
            try:
                snap = service.snapshot()
                assert snap['version'] == 1 and snap['generation'] == store.generation
                assert snap['current']['state'] == 'provisional' and snap['current']['time_s'] is None
                assert 'trace' not in snap['current'] and 'laps' not in snap
                h = service.history(None)
                assert h['changed'] and len(h['laps']) == 4
                same = service.history(h['revision'])
                assert not same['changed'] and 'laps' not in same and 'sessions' not in same
                store._publish()
                assert not service.history(h['revision'])['changed'], 'instrument publication must not resend history'
                raw = api.public_lap(dict(store.detail(h['laps'][0]['id'])['lap'],
                    secret='PRIVATE', path='PRIVATE', rec='PRIVATE', recording_session=999,
                    trace=[dict(d=0,t=0,speed=1,path='PRIVATE')]))
                assert 'PRIVATE' not in json.dumps(raw) and 'recording_session' not in raw
            finally: service.close()


def t_shared_comparison_matches_control_center_and_observed_prefix():
    with tempfile.TemporaryDirectory() as d:
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), runtime_base=d, queue_max=4096) as store:
            feed(store, write_circle_recording(d))
            service = api.Service(store)
            try:
                snap = store.snapshot(); a, b = [r for r in snap['laps'] if r['comparison_eligible']][:2]
                ra, rb = store.detail(a['id'])['lap'], store.detail(b['id'])['lap']
                result = wait_ready(lambda: service.compare(a['id'], b['id'], lambda _id: None))
                assert result['state'] == 'ready' and result['compatible']
                assert result['delta'] == gt7_comparison.delta(ra['trace'], rb['trace'])
                assert result['mini_sectors']['a'] == ra['sectors']
                current = snap['current']
                result = wait_ready(lambda: service.compare(a['id'], current['id'], lambda _id: None))
                assert result['compatible'] and result['provisional'] and result['lap_delta_s'] is None
                assert all(v is None for v in result['mini_sectors']['b']), 'untravelled complete sectors must stay empty'
                assert all(p['d'] <= current['distance_m'] for p in result['delta'])
                bad = dict(rb, compound='RS'); ref = dict(ra, compound='RM')
                assert not gt7_comparison.compare(ref, bad)['compatible']
                unknown = dict(rb, track_id=None, rec='other', session=90)
                assert not gt7_comparison.compare(dict(ra, track_id=None), unknown)['compatible']
            finally: service.close()


def t_http_current_access_reference_revoke_and_query_boundaries():
    import threading
    import urllib.request
    import urllib.error
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as d:
        root = Path(d); recdir = root / 'telemetry-recordings'; recdir.mkdir()
        path = write_circle_recording(str(recdir))
        source = gt7_context.source_identity(path)
        settings = access.Store(root, available=True)
        settings.update(enabled=True, grant=source, expected=0)
        crew = gate.m.CrewSource(''); crew.inject_row(1, name='Engineer', race_engineer=True)
        crew.inject_row(2, name='Owner', producer=True); crew.inject_row(3, name='Director', director=True)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), runtime_base=d, queue_max=4096) as store:
            feed(store, path); store.api = api.Service(store)
            relay = gate.m.Relay(gate._FakeSource([], []), [53001,53002], d)
            handler = gate.m.make_handler(relay, console_secret=gate.SECRET, crew_source=crew,
                engineer_access=settings, telemetry_store=SimpleNamespace(engineer=store))
            server = gate.m.ThreadingHTTPServer(('127.0.0.1',0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(route, query='', subject='engineer', method='GET'):
                token = gate.m.console_auth.mint_token(gate.SECRET, subject) if subject else ''
                url = f'http://127.0.0.1:{server.server_port}/console/race-engineer/{route}?t={token}' + ('&'+query if query else '')
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=3) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as response:
                    return response.code, json.load(response)
            try:
                status, snap = request('snapshot'); assert status == 200 and snap['instruments']['rpm'] == 7000
                for route, query in [('snapshot',''), ('history',''), ('lap','id='+snap['current']['id']),
                                     ('reference','id='+source), ('compare','a='+snap['current']['id']+'&b='+snap['current']['id'])]:
                    status, data = request(route,query,subject='director')
                    assert status == 403 and data['error']['code'] == 'forbidden'
                    assert request(route,query,subject='owner')[0] == 200
                    status, missing = request(route,query,subject=None)
                    assert status == 401 and missing['error']['code'] == 'unauthorized'
                for query in ('profile=other','path=../private','since=-1','since=0&since=1'):
                    status, data = request('history',query)
                    assert status == 400 and data['error']['code'] == 'invalid_query'
                assert request('export')[0] == 404 and request('snapshot',method='POST')[0] == 404
                ref = wait_ready(lambda: request('reference','id='+source)[1])
                assert ref['state'] == 'ready' and ref['laps']
                archived = next(r['id'] for r in ref['laps'] if r['comparison_eligible'])
                detail = wait_ready(lambda: request('lap','id='+archived)[1]); assert detail['state'] == 'ready'
                assert 'rec' not in detail['lap'] and 'recording_session' not in detail['lap']
                status, missing = request('reference','id='+'f'*32)
                assert status == 410 and missing['error']['code'] == 'reference_unavailable'
                status, missing = request('lap','id=r:'+'f'*32+':1:2')
                assert status == 410 and missing['error']['code'] == 'reference_unavailable'
                status, missing = request('lap','id=../private')
                assert status == 400 and missing['error']['code'] == 'invalid_query'
                original_reference = store.api.reference
                def revoking_reference(source_id, source_path):
                    result = original_reference(source_id,source_path)
                    settings.update(revoke=source,expected=1)
                    return result
                store.api.reference = revoking_reference
                status, missing = request('reference','id='+source)
                assert status == 410 and missing['error']['code'] == 'reference_unavailable'
                store.api.reference = original_reference
                settings.update(grant=source,expected=2)
                settings.update(revoke=source, expected=3)
                for route, query in [('reference','id='+source),('lap','id='+archived),
                                     ('compare','a='+archived+'&b='+snap['current']['id'])]:
                    status, data = request(route,query)
                    assert status == 410 and data['error']['code'] == 'reference_unavailable'
                crew.inject_row(1,race_engineer=False)
                status, data = request('snapshot'); assert status == 403 and data['error']['code'] == 'forbidden'
                crew.inject_row(1,race_engineer=True)
                settings.update(enabled=False, expected=4)
                status, data = request('snapshot'); assert status == 403 and data['error']['code'] == 'feature_disabled'
            finally:
                server.shutdown(); server.server_close()
                store.api.close()


def t_shared_pending_work_is_bounded_and_readers_do_not_block_ingestion():
    import threading
    import concurrent.futures
    with tempfile.TemporaryDirectory() as d:
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), queue_max=4096) as store:
            path = write_circle_recording(d); feed(store,path)
            service = api.Service(store); entered, release = threading.Event(), threading.Event()
            original = service._prepare; calls = []
            def slow(rows):
                calls.append(1); entered.set(); assert release.wait(5)
                return original(rows)
            service._prepare = slow
            try:
                laps = [r for r in store.snapshot()['laps'] if r['comparison_eligible']]
                a, b = laps[0]['id'], laps[1]['id']
                assert service.compare(a,b,lambda _id: None)['state'] == 'pending'
                assert entered.wait(2)
                started = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as readers:
                    results = list(readers.map(lambda _: service.compare(a,b,lambda _id: None), range(24)))
                assert time.monotonic() - started < 1, 'HTTP readers waited for analysis'
                assert all(r['state'] == 'pending' for r in results) and len(calls) == 1
                assert service.snapshot()['current'] and service.history(None)['laps']
                assert len(service._pending) == 1 and service._queue.maxsize == 16
                release.set()
                result = wait_ready(lambda: service.compare(a,b,lambda _id: None))
                assert result['state'] == 'ready' and len(calls) == 1
                result['a']['trace'].clear()
                assert service.compare(a,b,lambda _id: None)['a']['trace'], 'clients mutated shared cache'
            finally:
                release.set(); service.close()


def t_notes_context_and_definition_edits_change_prepared_analysis():
    import gt7_track_definitions as td
    from test_gt7_track_definitions import definition
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        context = gt7_context.Store.for_recording(path)
        doc = context.read(); data = doc['data']
        data['notes'] = [{'id':'note-a','scope':'lap','session':1,'lap':2,'text':'Declared coaching note'}]
        context.save(data, doc['revision'])
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), runtime_base=d, queue_max=4096) as store:
            store.bind_recording(path); feed(store,path); store._read_context()
            service = api.Service(store)
            try:
                h = service.history(None); row = next(r for r in h['laps'] if r['lap'] == 2)
                detail = service.lap(row['id'],lambda _id: None)['lap']
                assert detail['notes'][0]['text'] == 'Declared coaching note'
                assert 'session' not in detail['notes'][0]
                ds = td.Store(d, str(Path(d)/'bundled'))
                saved = ds.save(definition(),0)
                store._refresh_definitions(); store._read_context()
                detail = service.lap(row['id'],lambda _id: None)['lap']
                assert detail['larger_sectors']['reviewed'] and detail['corner_metrics'][0]['number'] == 1
                expected = td.annotate_index({'laps':[store.detail(row['id'])['lap']]}, data, ds, FakeTracks())['laps'][0]
                assert detail['larger_sectors'] == expected['larger_sectors']
                assert service.history(h['revision'])['changed']
                doc = context.read(); doc['data']['notes'][0]['text'] = 'New producer revision'
                context.save(doc['data'],doc['revision']); store._read_context()
                updated = service.lap(row['id'],lambda _id: None)['lap']
                assert updated['notes'][0]['text'] == 'New producer revision' and updated['context_revision'] != detail['context_revision']
                changed = definition(); changed['corners'][0]['number'] = 7
                ds.save(changed,saved['revision']); store._refresh_definitions(); store._read_context()
                updated = service.lap(row['id'],lambda _id: None)['lap']
                assert updated['definition_fingerprint'] != detail['definition_fingerprint']
                assert updated['corner_metrics'][0]['number'] == 7
            finally: service.close()


def t_preparation_cache_bounds_apply_to_failures_and_large_results():
    with live.Store(lambda: True) as store:
        service = api.Service(store)
        try:
            for n in range(30):
                service._remember('small'+str(n), dict(state='unavailable'))
            assert len(service._cache) <= 16
            for n in range(30):
                service._remember(str(n), dict(state='unavailable', data='x' * 1200000))
            assert len(service._cache) <= 16 and sum(len(json.dumps(r).encode()) for r in service._cache.values()) <= 16777216
            service._remember('oversized',dict(state='ready', data='x' * 2200000))
            assert service._cache['oversized']['state'] == 'unavailable'
        finally: service.close()


def t_optional_definition_permissions_do_not_stop_relay_collection():
    from unittest.mock import patch
    import os
    original = os.stat
    def denied(path,*args,**kwargs):
        if 'track-definitions' in str(path):
            raise PermissionError('optional definition permission denied')
        return original(path,*args,**kwargs)
    with tempfile.TemporaryDirectory() as d:
        with patch.object(live.os,'stat',side_effect=denied):
            with live.Store(lambda: False, tracks=FakeTracks(), cars=FakeCars(), runtime_base=d) as store:
                assert store._worker.is_alive() and store._reader.is_alive()
                store._refresh_definitions()
                assert store.snapshot()['enabled'] is False


def t_recorded_sector_selection_uses_recording_session_and_keeps_live_identity():
    import copy
    import gt7_track_definitions as td
    from test_gt7_track_definitions import definition, lap
    with tempfile.TemporaryDirectory() as d:
        definitions = td.Store(d, str(Path(d)/'bundle'))
        data = definition(); chosen = copy.deepcopy(data['variants'][0])
        chosen.update(id='producer-choice',name='Producer chosen sectors')
        data['variants'].append(chosen); definitions.save(data,0)
        # Collection has already reached live session 2; a recorder started there
        # has its own session 1. Producer choices belong to the recording context.
        row = lap(2,(4,4,4,4)); row.update(session=2,recording_session=1,
            id='a'*32+':l4',session_id='a'*32+':s3',_context_source='prepared')
        context = {'notes': [], 'sessions': {}, 'track_definition': {'selections': {
            '1': {'layout_id':'ring01','reverse':False,'variant_id':'producer-choice'}}}}
        with live.Store(lambda: True,tracks=FakeTracks(),runtime_base=d) as store:
            store._context = context
            annotated = store._annotate(row)
            assert annotated['sector_variant_id'] == 'producer-choice', 'live counter selected the wrong recording-context variant'
            assert annotated['session'] == 2 and annotated['recording_session'] == 1
            assert annotated['id'] == row['id'] and annotated['session_id'] == row['session_id']
            expected = td.annotate_index({'laps':[dict(row,session=1)]},context,definitions,FakeTracks())['laps'][0]
            assert annotated['larger_sectors'] == expected['larger_sectors']


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn): fn(); print('PASS', name)

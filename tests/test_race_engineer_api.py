#!/usr/bin/env python3
"""Versioned public projections, shared math and signed request-level authorization."""
import json
import os
import threading
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


def wait_ready(fn, matches=None):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        result = fn()
        if result['state'] != 'pending':
            if matches is None or matches(result):
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


def t_session_car_and_measured_position_survive_the_public_projection():
    import gt7_telemetry
    from test_gt7_laps import _pkt
    with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars()) as store:
        packet = gt7_telemetry.parse_packet(_pkt(1, 30, .5))
        store.put(packet, 100)
        assert store.flush(10)
        service = api.Service(store)
        try:
            history = service.history(None)
            assert history['sessions'][0]['laps'] == 0
            assert history['sessions'][0].get('car') == service.snapshot()['current']['car'], 'public session lost its vehicle name'
            assert 'Lancer' in history['sessions'][0]['car']
            position = service.snapshot()['instruments']['position']
            assert position == dict(x=packet.pos_x, z=packet.pos_z)
            store.put(packet._replace(pos_x=float('nan')), 101)
            assert store.flush(10)
            assert service.snapshot()['instruments']['position'] is None
            store.put(packet._replace(on_track=False), 102)
            assert store.flush(10)
            assert service.snapshot()['instruments']['position'] is None
            store.put(packet._replace(loading=True), 103)
            assert store.flush(10)
            assert service.snapshot()['instruments']['position'] is None
        finally:
            service.close()


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


def t_growing_provisional_pair_serves_completed_prefix_and_refreshes():
    import gt7_recording
    import gt7_telemetry
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d, n=200)
        packets = [(wall, gt7_telemetry.parse_packet(plain))
                   for wall, _, plain in gt7_recording.Recording(path).packets()]
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), runtime_base=d, queue_max=4096) as store:
            service = api.Service(store)
            try:
                initial = [(wall, packet) for wall, packet in packets if packet.lap < 4]
                growing = [(wall, packet) for wall, packet in packets if packet.lap == 4]
                for wall, packet in initial + growing[:40]:
                    store.put(packet, wall)
                assert store.flush(10)
                snap = store.snapshot()
                a = next(row["id"] for row in snap["laps"] if row["comparison_eligible"])
                b = snap["current"]["id"]
                query = lambda: service.compare(a, b, lambda _: None)
                previous = wait_ready(query)
                assert previous["compatible"] and previous["provisional"]
                for start in (40, 80, 120):
                    for wall, packet in growing[start:start+40]:
                        store.put(packet, wall)
                    assert store.flush(10)
                    current = store.snapshot()["current"]
                    assert current["track_definition"]["storage_source"] == "unsupported"
                    assert current["definition_fingerprint"] != previous["b"]["definition_fingerprint"], "fixture must also grow its automatic geometry proposal"
                    assert current["id"] == b and current["state"] == "provisional"
                    result = query()
                    assert result["state"] == "ready", "growing trace hid the completed pair"
                    assert result["provisional"] and result["mini_sectors"]["a"]
                    assert result["delta"] and result["b"]["distance_m"] <= current["distance_m"]
                    latest = wait_ready(query, matches=lambda r, current=current:
                        r.get("b", {}).get("distance_m") == current["distance_m"])
                    assert latest["b"]["distance_m"] > previous["b"]["distance_m"]
                    assert any(v is not None for v in latest["mini_sectors"]["b"])
                    previous = latest
                original = service._detail
                for field, value in (("context_revision", 999), ("definition_fingerprint", "new-definition"),
                                     ("strategy", {"fuel_map": 7}), ("notes", [{"id": "new", "scope": "lap", "text": "Changed"}])):
                    def revised(identity, reference, field=field, value=value):
                        detail = original(identity, reference)
                        if identity == b:
                            detail["lap"][field] = value
                            if field == "definition_fingerprint":
                                detail["lap"]["track_definition"]["storage_source"] = "override"
                        return detail
                    service._detail = revised
                    assert query()["state"] == "pending", "semantic change reused stale provisional analysis"
                    changed = wait_ready(query)
                    assert changed["b"][field] == value
                service._detail = original
            finally:
                service.close()


def t_provisional_refresh_coalesces_readers_while_preparation_is_blocked():
    with tempfile.TemporaryDirectory() as d:
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), queue_max=4096) as store:
            feed(store, write_circle_recording(d))
            service = api.Service(store)
            release, entered = threading.Event(), threading.Event()
            try:
                snap = store.snapshot()
                a = next(row["id"] for row in snap["laps"] if row["comparison_eligible"])
                b = snap["current"]["id"]
                query = lambda: service.compare(a, b, lambda _: None)
                before = wait_ready(query)
                original_detail, original_prepare = service._detail, service._prepare
                revision = [0]
                calls = []
                def growing(identity, reference):
                    result = original_detail(identity, reference)
                    if identity == b:
                        result["lap"]["distance_m"] += revision[0]
                    return result
                def held(rows):
                    calls.append(1)
                    entered.set()
                    assert release.wait(5)
                    return original_prepare(rows)
                service._detail, service._prepare = growing, held
                revision[0] = 10
                assert query()["state"] == "ready"
                assert entered.wait(2)
                started = time.monotonic()
                for n in range(20, 60):
                    revision[0] = n
                    result = query()
                    assert result["b"]["distance_m"] == before["b"]["distance_m"]
                assert time.monotonic() - started < 1
                assert len(calls) == 1 and len(service._pending) == 1 and service._queue.qsize() == 0
                release.set()
                latest = wait_ready(query, matches=lambda r:
                    r.get("b", {}).get("distance_m") == before["b"]["distance_m"] + revision[0])
                assert latest["provisional"]
            finally:
                release.set()
                service.close()


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
            store.bind_recording(path); feed(store,path)
            service = api.Service(store)
            try:
                h = service.history(None); row = next(r for r in h['laps'] if r['lap'] == 2)
                detail = wait_ready(lambda: service.lap(row['id'],lambda _id: None),
                    matches=lambda r: r.get('lap',{}).get('context_revision') == doc['revision']+1)['lap']
                assert detail['notes'][0]['text'] == 'Declared coaching note'
                assert 'session' not in detail['notes'][0]
                ds = td.Store(d, str(Path(d)/'bundled'))
                saved = ds.save(definition(),0)
                detail = wait_ready(lambda: service.lap(row['id'],lambda _id: None),
                    matches=lambda r: r.get('lap',{}).get('definition_fingerprint') == td.fingerprint(saved['definition']))['lap']
                assert detail['larger_sectors']['reviewed'] and detail['corner_metrics'][0]['number'] == 1
                expected = td.annotate_index({'laps':[store.detail(row['id'])['lap']]}, data, ds, FakeTracks())['laps'][0]
                assert detail['larger_sectors'] == expected['larger_sectors']
                assert service.history(h['revision'])['changed']
                doc = context.read(); doc['data']['notes'][0]['text'] = 'New producer revision'
                context.save(doc['data'],doc['revision'])
                updated = wait_ready(lambda: service.lap(row['id'],lambda _id: None),
                    matches=lambda r: r.get('lap',{}).get('context_revision') == doc['revision']+1)['lap']
                assert updated['notes'][0]['text'] == 'New producer revision' and updated['context_revision'] != detail['context_revision']
                changed = definition(); changed['corners'][0]['number'] = 7
                saved = ds.save(changed,saved['revision'])
                updated = wait_ready(lambda: service.lap(row['id'],lambda _id: None),
                    matches=lambda r: r.get('lap',{}).get('definition_fingerprint') == td.fingerprint(saved['definition']))['lap']
                assert updated['definition_fingerprint'] != detail['definition_fingerprint']
                assert updated['corner_metrics'][0]['number'] == 7
            finally: service.close()


def t_definition_replacement_with_equal_size_and_mtime_invalidates_analysis():
    import gt7_track_definitions as td
    from test_gt7_track_definitions import definition
    with tempfile.TemporaryDirectory() as d:
        path=write_circle_recording(d);ds=td.Store(d,str(Path(d)/'bundled'))
        saved=ds.save(definition(),0)
        with live.Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),runtime_base=d,queue_max=4096) as store:
            store.bind_recording(path);feed(store,path)
            store.close()  # Isolate invalidation from the periodic worker's timing.
            store._refresh_definitions();store._read_context()
            row=next(r for r in store.history(None)['laps'] if r['lap']==2)
            before=store.detail(row['id'])['lap']
            recovery_stamp=store._record_stamp(gt7_context.source_identity(path),path)
            filename=ds.path('ring01',False);original=os.stat(filename)
            changed=definition();changed['corners'][0]['number']=7
            ds.save(changed,saved['revision'])
            os.utime(filename,ns=(original.st_atime_ns,original.st_mtime_ns))
            replacement=os.stat(filename)
            assert replacement.st_size==original.st_size and replacement.st_mtime_ns==original.st_mtime_ns
            assert replacement.st_ino!=original.st_ino, 'fixture did not atomically replace the definition'
            store._refresh_definitions();store._read_context()
            after=store.detail(row['id'])['lap']
            assert after['definition_fingerprint']!=before['definition_fingerprint'], 'equal size/mtime hid a replaced definition'
            assert after['corner_metrics'][0]['number']==7
            assert store._record_stamp(gt7_context.source_identity(path),path)!=recovery_stamp


def t_recorded_definition_replacement_invalidates_prepared_cache():
    import gt7_track_definitions as td
    from test_gt7_track_definitions import definition
    with tempfile.TemporaryDirectory() as d:
        path=write_circle_recording(d);source=gt7_context.source_identity(path)
        ds=td.Store(d,str(Path(d)/'bundled'));saved=ds.save(definition(),0)
        with live.Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),runtime_base=d) as store:
            refresh=store._refresh_definitions
            store._refresh_definitions=lambda:None  # Keep the timestamp collision deterministic.
            service=api.Service(store)
            try:
                query=lambda:service.lap(f'r:{source}:1:2',lambda _:path)
                before=wait_ready(query)['lap'];replays=store.stats()['replays']
                filename=ds.path('ring01',False);original=os.stat(filename)
                changed=definition();changed['corners'][0]['number']=7
                saved=ds.save(changed,saved['revision'])
                os.utime(filename,ns=(original.st_atime_ns,original.st_mtime_ns));refresh()
                after=wait_ready(query,matches=lambda r:r.get('lap',{}).get('definition_fingerprint')==td.fingerprint(saved['definition']))['lap']
                assert after['corner_metrics'][0]['number']==7
                assert before['definition_fingerprint']!=after['definition_fingerprint']
                assert store.stats()['replays']>replays, 'old prepared cache was reused'
            finally:service.close()


def t_recorded_context_replacement_with_equal_stat_invalidates_preparation():
    with tempfile.TemporaryDirectory() as d:
        path=write_circle_recording(d);source=gt7_context.source_identity(path)
        context=gt7_context.Store.for_recording(path)
        data=context.read()['data'];data['notes']=[{'id':'n','scope':'recording','text':'Old'}]
        saved=context.save(data,0)
        with live.Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),runtime_base=d) as store:
            service=api.Service(store)
            try:
                query=lambda:service.lap(f'r:{source}:1:2',lambda _:path)
                before=wait_ready(query)['lap'];original=os.stat(context.path)
                original_doc=json.loads(Path(context.path).read_text())
                data['notes'][0]['text']='New';context.save(data,saved['revision'])
                replacement=json.loads(Path(context.path).read_text())
                replacement['updated_at']=original_doc['updated_at']
                gt7_context._atomic(context.path,replacement)
                os.utime(context.path,ns=(original.st_atime_ns,original.st_mtime_ns))
                assert os.stat(context.path).st_size==original.st_size
                after=wait_ready(query)['lap']
                assert after['context_revision']!=before['context_revision'], 'equal-stat context replacement reused old preparation'
                assert after['notes'][0]['text']=='New'
            finally:service.close()


def t_expected_definition_waits_for_worker_publication_in_progress():
    import gt7_track_definitions as td
    from test_gt7_track_definitions import definition
    with tempfile.TemporaryDirectory() as d:
        path=write_circle_recording(d);ds=td.Store(d,str(Path(d)/'bundled'))
        saved=ds.save(definition(),0)
        with live.Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),runtime_base=d,queue_max=4096) as store:
            store.bind_recording(path);feed(store,path)
            row=next(r for r in store.history(None)['laps'] if r['lap']==2)
            service=api.Service(store);entered=threading.Event();release=threading.Event()
            original=store._annotate
            def held(row):
                if threading.current_thread() is store._worker and not release.is_set():
                    entered.set();assert release.wait(5), 'publication barrier not released'
                return original(row)
            try:
                before=service.lap(row['id'],lambda _:None)['lap']
                store._annotate=held
                changed=definition();changed['corners'][0]['number']=7
                updated=ds.save(changed,saved['revision'])
                assert entered.wait(5), 'worker did not start updating definition'
                store._read_context()  # Reproduces the old test's concurrent direct call.
                assert service.lap(row['id'],lambda _:None)['lap']['definition_fingerprint']==before['definition_fingerprint']
                def read_and_release():
                    result=service.lap(row['id'],lambda _:None)
                    release.set()
                    return result
                result=wait_ready(read_and_release,matches=lambda result:
                    result['lap']['definition_fingerprint']==td.fingerprint(updated['definition']))
                assert result['lap']['corner_metrics'][0]['number']==7
            finally:
                release.set();service.close()


def t_preparation_cache_bounds_apply_to_failures_and_large_results():
    with live.Store(lambda: True) as store:
        service = api.Service(store)
        try:
            for n in range(30):
                service._remember('small'+str(n), dict(state='unavailable'))
            assert len(service._cache) <= 16 and set(service._versions) == set(service._cache)
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


def t_unknown_track_recovery_keeps_the_live_pool_without_relabeling_archive_rows():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        with live.Store(lambda: True,tracks=FakeTracks(known=False),cars=FakeCars(),
                        details_max=1,queue_max=4096) as store:
            feed(store,path); before = store.snapshot()
            old = next(r for r in before['laps'] if r['lap'] == 2)
            store.bind_recording(path)
            restored = wait_ready(lambda:store.detail(old['id']))['lap']
            current = store.snapshot()['current']
            assert restored['session_id'] == current['session_id']
            assert gt7_comparison.compare(restored,current)['compatible'], 'recovery changed the unknown-track live cohort'
            assert restored['rec'] == store.generation and restored['session'] == old['session']
            mapped_identity = dict(old,session=2,recording_session=1,session_id=store.generation+':s999')
            mapped = store.recording_detail(gt7_context.source_identity(path),path,1,2,identity=mapped_identity)['lap']
            mapped_current = dict(current,session=2,session_id=mapped_identity['session_id'])
            assert gt7_comparison.compare(mapped,mapped_current)['compatible'], 'recovery lost the independent live session counter'
            assert mapped['session'] == 2 and mapped['recording_session'] == 1
            archived = store.recording_detail(gt7_context.source_identity(path),path,1,2)['lap']
            assert archived['rec'] == gt7_context.source_identity(path) and archived['session'] == 1
            assert not gt7_comparison.compare(archived,current)['compatible'], 'unknown archive sources must not enter the live pool'


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn): fn(); print('PASS', name)

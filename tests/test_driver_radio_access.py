#!/usr/bin/env python3
"""Driver radio contracts, independent profile grants and real signed HTTP."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import driver_radio_access as radio


def rejected(fn, code):
    try:
        fn()
    except radio.RadioError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError('Expected ' + code)


def t_access_is_explicit_profile_scoped_and_revocable():
    with tempfile.TemporaryDirectory() as d:
        store = radio.Store(Path(d) / 'solo', available=True)
        rejected(lambda: store.authorize('driver'), 'feature_disabled')
        doc = store.update({'expected': 0, 'enabled': True, 'drivers': ['driver']})
        assert doc['revision'] == 1
        assert store.authorize('driver') == {'version': 1, 'enabled': True, 'revision': 1}
        for subject in ('director', 'engineer', 'producer'):
            rejected(lambda subject=subject: store.authorize(subject), 'forbidden')
        rejected(lambda: radio.Store(Path(d) / 'other', True).authorize('driver'), 'feature_disabled')
        rejected(lambda: radio.Store(Path(d) / 'solo', False).authorize('driver'), 'source_unavailable')
        rejected(lambda: store.update({'expected': 0, 'enabled': False}), 'conflict')
        rejected(lambda: radio.Store(Path(d) / 'solo', False).update({'expected': 1, 'enabled': False}), 'source_unavailable')
        store.update({'expected': 1, 'drivers': []})
        rejected(lambda: store.authorize('driver'), 'forbidden')
        store.path.write_text('{"enabled":true}', encoding='utf8')
        rejected(store.read, 'settings_unavailable')


def t_settings_symlink_cannot_import_foreign_profile_grants():
    import os
    if not hasattr(os, 'symlink'):
        return
    with tempfile.TemporaryDirectory() as d:
        other = radio.Store(Path(d) / 'other', True)
        other.update({'expected': 0, 'enabled': True, 'drivers': ['driver']})
        store = radio.Store(Path(d) / 'solo', True)
        store.path.parent.mkdir()
        try:
            store.path.symlink_to(other.path)
        except OSError:
            return  # Windows hosts without symlink privilege.
        rejected(lambda: store.authorize('driver'), 'settings_unavailable')


def t_settings_validate_and_native_configuration_is_separate():
    with tempfile.TemporaryDirectory() as d:
        store = radio.Store(d, True)
        assert store.read()['voice'] == {'enabled': False, 'microphone': None, 'input_language': 'en'}
        doc = store.update({'expected': 0, 'voice': {'enabled': True, 'microphone': 'USB Mic', 'input_language': 'de'}})
        assert doc['voice']['input_language'] == 'de' and not doc['enabled']
        for change in ({'enabled': 'yes'}, {'drivers': ['../other']}, {'drivers': ['driver-' + str(i) for i in range(129)]},
                       {'voice': {'enabled': True, 'microphone': None, 'input_language': 'fr'}},
                       {'voice': {'enabled': True, 'microphone': 'x' * 257, 'input_language': 'en'}},
                       {'archive_grants': []}, {'expected': True}):
            rejected(lambda change=change: store.update({'expected': 1, **change}), 'invalid_settings' if 'expected' not in change else 'conflict')
        assert store.read()['revision'] == 1
        store.path.write_text(json.dumps(store.read()) + ' ' * 16385, encoding='utf8')
        rejected(store.read, 'settings_unavailable')


def t_setup_revision_binding_and_service():
    state = radio.Session()
    binding = dict(generation='relay-one', session_id='session-one', context_revision=0)
    assert state.read(binding)['setup'] == {}
    first = state.update(binding, {'binding': binding, 'expected': 0, 'setup': {
        'laps': 20, 'starting_compound': 'RM', 'fuel_reserve_l': 2}})
    assert first['setup']['laps'] == 20 and first['service'] is None
    rejected(lambda: state.update(binding, {'binding': binding, 'expected': 0, 'setup': {}}), 'conflict')
    confirmed = state.update(binding, {'binding': binding, 'expected': 1, 'compound': 'RH'}, service=True, lap_id='L4')
    assert confirmed['service']['compound'] == 'RH' and confirmed['service']['lap_id'] == 'L4'
    assert confirmed['service']['confirmed_at'] > 0
    changed = state.update(binding, {'binding': binding, 'expected': 2, 'setup': {'duration_min': 60}})
    assert changed['service'] == confirmed['service'] and changed['setup'] == {'duration_min': 60}
    for key, value in (('generation', 'relay-two'), ('session_id', 'session-two'), ('context_revision', 1)):
        next_binding = dict(binding, **{key: value})
        result = state.read(next_binding)
        assert result['setup'] == {} and result['service'] is None
        rejected(lambda next_binding=next_binding, binding=binding, result=result: state.update(next_binding, {'binding': binding, 'expected': result['revision'], 'setup': {}}), 'conflict')
        binding = next_binding
    rejected(lambda: state.update(None, {'binding': None, 'expected': 0, 'setup': {}}), 'session_unavailable')


def t_setup_rejects_unknown_or_unsafe_fields_without_writing():
    binding = dict(generation='g', session_id='s', context_revision=0)
    state = radio.Session()
    for setup in ({'laps': True}, {'laps': -1}, {'laps': 1.5}, {'laps': 100001},
                  {'duration_min': float('nan')}, {'duration_min': float('inf')},
                  {'laps': 2, 'duration_min': 10}, {'starting_compound': 'ideal'},
                  {'fuel_reserve_l': -1}, {'fuel_reserve_l': True}, {'fuel_reserve_l': 10 ** 400}, {'weather': 'clear'}):
        rejected(lambda setup=setup: state.update(binding, {'binding': binding, 'expected': 0, 'setup': setup}), 'invalid_body')
        assert state.read(binding)['revision'] == 0 and state.read(binding)['setup'] == {}
    rejected(lambda: state.update(binding, {'binding': binding, 'expected': 0, 'compound': 'RM', 'confirmed_at': 0}, service=True), 'invalid_body')
    rejected(lambda: state.update(binding, {'binding': binding, 'expected': 0, 'compound': 'invalid'}, service=True), 'invalid_body')
    assert state.read(binding)['service'] is None


def t_radio_only_opt_in_collects_live_session_without_engineer_access():
    import os
    import gt7_telemetry as tm
    import test_console_gate as fixture
    from test_gt7_laps import _pkt
    with tempfile.TemporaryDirectory() as d:
        engineer_access = fixture.m.race_engineer_access.Store(d, True)
        radio_access = radio.Store(d, True)
        telemetry = tm.TelemetryStore()
        store = fixture.m.engineer_live_for(engineer_access, telemetry, os.path.join(d, 'telemetry-recordings'), d, None)
        try:
            pkt = tm.parse_packet(_pkt(1, 30, 0))
            assert not store.put(pkt, 100)
            radio_access.update({'expected': 0, 'enabled': True, 'drivers': ['driver']})
            store.refresh_enabled()
            telemetry.update(pkt, 100)
            assert store.flush()
            assert store.snapshot()['session_id'] is not None, 'Radio-only opt-in must ingest a session'
            assert telemetry.recorder is None
            try:
                engineer_access.authorize({'race_engineer'})
            except fixture.m.race_engineer_access.AccessError as exc:
                assert exc.code == 'feature_disabled'
            else:
                raise AssertionError('Radio opt-in must not grant Engineer access')
            radio_access.update({'expected': 1, 'enabled': False})
            store.refresh_enabled()
            assert not store.put(pkt, 101)
        finally:
            store.close()


def t_signed_http_boundaries_and_session_writes():
    import http.client
    import threading
    from types import SimpleNamespace
    import test_console_gate as fixture
    m = fixture.m
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / 'solo'
        access = radio.Store(root, True)
        crew = m.CrewSource('')
        crew.inject_row(1, name='Director', director=True)
        crew.inject_row(2, name='Engineer', race_engineer=True)
        crew.inject_row(3, name='Producer', producer=True)
        engineer_access = m.race_engineer_access.Store(root, True)
        engineer_access.update(enabled=True, expected=0)
        snapshot = dict(generation='g', session_id='s', context_revision=0, current={'id': 'L4'})
        engineer = SimpleNamespace(snapshot=lambda compact=False: dict(snapshot))
        handler = m.make_handler(m.Relay(fixture._FakeSource([], []), [53001, 53002], d),
                                 console_secret=fixture.SECRET, crew_source=crew,
                                 engineer_access=engineer_access,
                                 telemetry_store=SimpleNamespace(engineer=engineer))
        server = m.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def request(path='/console/driver-radio/access', subject='driver', method='GET', body=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            hdr = {'Content-Type': 'application/json'}
            if subject:
                hdr['Cookie'] = 'rc_console=' + m.console_auth.mint_token(fixture.SECRET, subject)
            hdr.update(headers or {})
            try:
                conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=hdr)
                response = conn.getresponse()
                raw = response.read()
                return response.status, json.loads(raw), response.getheader('Cache-Control')
            finally:
                conn.close()
        def error(code, status, *args, **kwargs):
            got, doc, cache = request(*args, **kwargs)
            assert got == status and doc['error']['code'] == code, (got, doc)
            assert cache == 'no-store'
        try:
            error('unauthorized', 401, subject=None)
            error('feature_disabled', 403)
            access.update({'expected': 0, 'enabled': True, 'drivers': ['driver']})
            status, doc, cache = request()
            assert status == 200 and doc == {'version': 1, 'enabled': True, 'revision': 1} and cache == 'no-store', (status, doc)
            status, native, _ = request('/console/driver-radio/native')
            assert status == 200 and native['phase'] == 'disabled', (status, native)
            assert 'microphone' not in native and 'google_key' not in native
            control = dict(binding=native['binding'], expected=native['revision'], paused=True)
            status, changed, _ = request('/console/driver-radio/native', method='POST', body=control)
            assert status == 200 and changed['paused'] is True, (status, changed)
            error('conflict', 409, path='/console/driver-radio/native', method='POST', body=control)
            error('foreign_origin', 403, path='/console/driver-radio/native', method='POST', body=control,
                  headers={'Origin':'https://foreign.invalid'})
            error('forbidden', 403, path='/console/driver-radio/native', subject='director')
            error('unauthorized', 401, path='/console/driver-radio/native', subject=None)
            for subject in ('director', 'engineer', 'producer', 'stranger'):
                error('forbidden', 403, subject=subject)
            for path in ('/console/race-engineer/access', '/console/next', '/console/obs/stint/live'):
                assert request(path)[0] == 403
            assert request('/driver-radio/access')[0] == 404
            error('not_found', 404, path='/console/driver-radio/admin')
            error('not_found', 404, path='/console/driver-radio/voice', method='POST', body={})
            admin_status, admin_doc, _ = request('/driver-radio/admin')
            assert admin_status == 200 and admin_doc['profile'] == 'solo'
            error('profile_changed', 409, path='/driver-radio/admin', method='POST',
                  body={'profile': 'other', 'expected': 1, 'enabled': False})
            assert access.read()['enabled'] is True
            for headers in ({'Host': 'foreign.example.test'}, {'Origin': 'https://foreign.example.test'}, {'Sec-Fetch-Site': 'cross-site'}):
                error('foreign_origin', 403, path='/driver-radio/admin', headers=headers)
            binding = {k: snapshot[k] for k in ('generation', 'session_id', 'context_revision')}
            body = dict(binding=binding, expected=0, setup={'laps': 12, 'starting_compound': 'RS'})
            error('foreign_origin', 403, path='/console/driver-radio/setup', method='POST', body=body,
                  headers={'Origin': 'https://foreign.example.test'})
            error('invalid_body', 400, path='/console/driver-radio/setup', method='POST', body=body,
                  headers={'Content-Type': 'text/plain'})
            status, doc, _ = request('/console/driver-radio/setup', method='POST', body=body)
            assert status == 200 and doc['setup']['laps'] == 12 and doc['revision'] == 1, (status, doc)
            error('conflict', 409, path='/console/driver-radio/setup', method='POST', body=body)
            error('invalid_body', 400, path='/console/driver-radio/setup', method='POST',
                  body=dict(binding=binding, expected=1, setup={'fuel_reserve_l': -1}))
            status, doc, _ = request('/console/driver-radio/service', method='POST',
                                     body=dict(binding=binding, expected=1, compound='RM'))
            assert status == 200 and doc['service']['lap_id'] == 'L4' and doc['revision'] == 2
            snapshot['session_id'] = 'new-session'
            status, doc, _ = request('/console/driver-radio/setup')
            assert status == 200 and doc['setup'] == {} and doc['service'] is None
            error('conflict', 409, path='/console/driver-radio/setup', method='POST', body=body)
            snapshot['session_id'] = None
            error('session_unavailable', 409, path='/console/driver-radio/setup', method='POST', body=body)
            access.update({'expected': 1, 'drivers': []})
            error('forbidden', 403)
            # A new handler reads the other profile, never the first profile's grants.
            other = radio.Store(Path(d) / 'other', True)
            other.update({'expected': 0, 'enabled': True})
            error('foreign_origin', 403, path='/driver-radio/admin', headers={'Referer': 'http://foreign.example.test/'})
            status, doc, _ = request('/driver-radio/admin', method='POST', body={'profile': 'solo', 'expected': 2, 'enabled': False})
            assert status == 200 and doc['enabled'] is False
            error('feature_disabled', 403)
            access.path.write_text('{bad', encoding='utf8')
            error('settings_unavailable', 503)
            error('settings_unavailable', 503, path='/driver-radio/admin')
        finally:
            server.shutdown()
            server.server_close()


def t_delayed_body_cannot_retain_revoked_token_or_assignment():
    import http.client
    import threading
    import test_console_gate as fixture
    m = fixture.m
    with tempfile.TemporaryDirectory() as d:
        access = radio.Store(d, True)
        access.update({'expected': 0, 'enabled': True, 'drivers': ['driver']})
        versions = str(Path(d) / 'console-versions.json')
        handler = m.make_handler(m.Relay(fixture._FakeSource([], []), [53001, 53002], d),
            console_secret=fixture.SECRET, console_versions_path=versions,
            engineer_access=m.race_engineer_access.Store(d, True))
        reached = threading.Event()
        original = handler._radio_body
        def announced_body(self):
            reached.set()
            return original(self)
        handler._radio_body = announced_body
        server = m.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for version, error, status in ((1, 'unauthorized', 401), (2, 'forbidden', 403)):
                reached.clear()
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                body = json.dumps({'binding': None, 'expected': 0, 'setup': {}}).encode()
                token = m.console_auth.mint_token(fixture.SECRET, 'driver', version)
                conn.putrequest('POST', '/console/driver-radio/setup')
                conn.putheader('Cookie', 'rc_console=' + token)
                conn.putheader('Content-Type', 'application/json')
                conn.putheader('Content-Length', str(len(body)))
                conn.endheaders()
                conn.send(body[:1])
                assert reached.wait(3), 'request did not reach body processing'
                if version == 1:
                    m.console_admin.bump_version(versions, 'driver')
                else:
                    access.update({'expected': 1, 'drivers': []})
                conn.send(body[1:])
                response = conn.getresponse()
                doc = json.loads(response.read())
                conn.close()
                assert response.status == status and doc['error']['code'] == error, (response.status, doc)
        finally:
            server.shutdown()
            server.server_close()


def t_native_service_selection_rejects_paths_unknown_engines_and_unbounded_controls():
    with tempfile.TemporaryDirectory() as d:
        store=radio.Store(d,True)
        for config in ({'wake':'custom phrase'},{'wake':[]},{'stt':'automatic_cloud_fallback'},
                       {'volume':True},{'volume':2},{'muted':1},{'executable':'/private/path'}):
            rejected(lambda value=config:store.update({'expected':0,'native':value}),'invalid_settings')
        state=store.update({'expected':0,'native':{'wake':'hey_engineer','stt':'whisper','volume':.3,'muted':False}})
        assert state['native']['stt']=='whisper' and state['revision']==1


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn()
            print('PASS', name)

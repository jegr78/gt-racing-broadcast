#!/usr/bin/env python3
"""New selection HTTP paths and selected optional-source reconstruction."""
import copy
import dataclasses
import http.client
import json
import tempfile
from urllib.parse import urlencode
from ai_ui_fixture import Fixture,web_fixture
import ai_package
import ai_reports
import gt7_recording


def t_selection_and_reference_http_semantics_and_origin():
    with tempfile.TemporaryDirectory() as root:
        fixture=Fixture(root);server,port=web_fixture._serve(fixture.context())
        def get(path,origin=None):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            try:
                connection.request('GET',path,headers={'Origin':origin} if origin else {})
                response=connection.getresponse();return response.status,json.loads(response.read())
            finally:connection.close()
        try:
            query=urlencode(dict(rec=fixture.source.name))
            status,doc=get('/api/ai/selection?'+query)
            assert status==200 and doc['ok'] and doc['sessions'][0]['laps']
            status,doc=get('/api/ai/references?'+query+'&session=1')
            assert status==200 and doc['ok'] and doc['suggestions']
            status,doc=get('/api/ai/references?'+query+'&session=invalid')
            assert status==400 and not doc['ok'] and doc['error']['code']=='invalid_selection'
            status,doc=get('/api/ai/selection?'+query,'https://foreign.example.test')
            assert status==403 and not doc['ok'] and doc['error']['code']=='foreign_origin',(status,doc)
            fixture.profile='other'
            status,doc=get('/api/ai/history?profile=profile')
            assert status==409 and doc['error']['code']=='profile_changed'
            fixture.profile='profile'
            settings=fixture.settings.read();settings['enabled']=False;fixture.settings.save(settings)
            status,doc=get('/api/ai/selection?'+query)
            assert status==403 and doc['error']['code']=='disabled'
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            connection.request('GET','/ai-analysis.js');response=connection.getresponse()
            assert response.status==200 and b'Post-session AI analysis' in response.read();connection.close()
        finally:server.shutdown();server.server_close()


def t_selected_shift_replay_and_optional_failure():
    import test_racecast
    race=test_racecast.m
    with tempfile.TemporaryDirectory() as root:
        fixture=Fixture(root);calls=[];original=race._telemetry_shift_analysis
        try:
            def shift(path,index,row):
                calls.append((row['session'],row['lap']))
                assert index['context_snapshot']['source_id']==fixture.source.identity
                return {'reference_id':'synthetic','reference':{'source':'synthetic'},'shifts':[]}
            source=dataclasses.replace(fixture.source,index=dict(fixture.source.index,context_snapshot=fixture.source.context))
            race._telemetry_shift_analysis=shift
            annotated=race._ai_enrich_source(source,1,[2])
            assert calls==[(1,2)] and annotated.index['shift_reference_snapshots']['synthetic']=={'source':'synthetic'}
            assert 'shift_analysis' not in source.index['laps'][0]
            def failed(*args):raise gt7_recording.RecordingError('synthetic damaged optional detail')
            race._telemetry_shift_analysis=failed
            partial=race._ai_enrich_source(source,1,[2])
            package=ai_package.build(partial,1,laps=[2])
            assert package['laps'][0]['derived']['shift_analysis']['unavailable']
            assert 'Optional shift detail unavailable for selected lap' in package['limitations']
        finally:race._telemetry_shift_analysis=original


def t_historical_shift_reconstruction_uses_the_same_selected_enricher():
    with tempfile.TemporaryDirectory() as root:
        fixture=Fixture(root);source=fixture.source;observed=2;calls=[]
        def enrich(source,session,laps):
            calls.append((session,laps));index=copy.deepcopy(source.index)
            for row in index['laps']:
                if row['session']==session and row['lap'] in laps:row['shift_analysis']={'observed_count':observed}
            return dataclasses.replace(source,index=index)
        package=ai_package.build(source,1,laps=[2],enrich_source=enrich)
        assert not ai_reports.stale(package,lambda name:source,enrich)['stale']
        observed=3
        assert ai_reports.stale(package,lambda name:source,enrich)['stale']
        assert calls==[(1,[2])]*3


if __name__=='__main__':
    for name,fn in sorted(globals().copy().items()):
        if name.startswith('t_'):fn();print('PASS',name)

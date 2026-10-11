#!/usr/bin/env python3
"""Real signed Console requests: role loss and profile feature revocation are live."""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_console_gate as fixture
import race_engineer_access as access
import test_racecast
import test_ai_package


def t_every_request_checks_identity_current_role_and_enablement():
    m=fixture.m
    with tempfile.TemporaryDirectory() as d:
        store=access.Store(d,available=True)
        crew=m.CrewSource('');crew.inject_row(1,name='Engineer',race_engineer=True)
        crew.inject_row(2,name='Owner',producer=True)
        src=fixture._FakeSource([],[])
        relay=m.Relay(src,[53001,53002],d)
        handler=m.make_handler(relay,console_secret=fixture.SECRET,crew_source=crew,
            console_page_path=str(Path(fixture.ROOT)/'src/console/console.html'),engineer_access=store)
        server=m.ThreadingHTTPServer(('127.0.0.1',0),handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        def request(subject=None,path='access',method='GET'):
            token=m.console_auth.mint_token(fixture.SECRET,subject) if subject else ''
            url='http://127.0.0.1:'+str(server.server_port)+'/console/race-engineer/'+path
            req=urllib.request.Request(url+'?t='+token,method=method)
            try:
                with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as response:
                raw=response.read()
                return response.code,json.loads(raw) if response.headers.get('Content-Type','').startswith('application/json') else {'unsupported_method':response.code}
        try:
            status,raw=fixture._get(server.server_port,'/crew/data')
            assert status==200 and json.loads(raw)['rows'][0]['race_engineer'] is True
            status,body=request();assert status==401
            status,body=request('engineer');assert status==403 and body['error']['code']=='feature_disabled'
            store.update(enabled=True,expected=0)
            status,body=request('engineer');assert status==200 and body['enabled'] and not body['analysis_available']
            status,body=request('owner');assert status==200
            status,body=request('unknown');assert status==403 and body['error']['code']=='forbidden'
            token=m.console_auth.mint_token(fixture.SECRET,'engineer')
            for path in ('/console/next','/console/telemetry/record/start','/console/obs/stint/live','/console/panel'):
                status,raw=fixture._get(server.server_port,path,token=token)
                assert status==403 and json.loads(raw)['error']=='forbidden',(path,status,raw)
            for method in ('POST','DELETE'):
                status,body=request('engineer',method=method);assert status in (404,501),body
                if method=='POST':assert body['error']['code']=='not_found'
                else:assert body['unsupported_method']==501
            crew.inject_row(1,race_engineer=False)
            # A legacy projection could belong to an older roster revision.
            original_get=crew.get
            crew.get=lambda:[('Engineer',False,True)]
            status,body=request('engineer');assert status==403 and body['error']['code']=='forbidden'
            crew.get=original_get
            crew.inject_row(1,race_engineer=True)
            crew._fetch_text=lambda timeout=15:None
            assert not crew.refresh()
            status,body=request('engineer');assert status==200
            crew._fetch_text=lambda timeout=15:'Name,Race Engineer,Discord\n'
            assert crew.refresh()
            status,body=request('engineer');assert status==403 and body['error']['code']=='forbidden'
            crew.inject_row(1,name='Engineer',race_engineer=True)
            store.update(enabled=False,expected=1)
            status,body=request('engineer');assert status==403 and body['error']['code']=='feature_disabled'
        finally:server.shutdown();server.server_close()


def t_producer_settings_bind_profile_and_stable_reference_identity():
    m=test_racecast.m
    with tempfile.TemporaryDirectory() as d:
        source=test_ai_package.source(Path(d)/'profile/telemetry-recordings')
        with patch.object(m,'_runtime_base_dir',return_value=d),patch.object(m,'_active_profile_name',return_value='profile'),patch.object(m,'_profile_has_telemetry',return_value=True):
            data=m.engineer_request_data(False,{'profile':'profile'})
            assert data['ok'] and data['profile']=='profile' and not data['enabled']
            assert data['recordings']==[{'id':source.identity,'name':source.name}]
            data=m.engineer_request_data(True,{'profile':'profile','expected':0,'enabled':True,'grant':source.identity})
            assert data['ok'] and data['enabled'] and len(data['grants'])==1
            data=m.engineer_request_data(False,{'profile':'other'})
            assert not data['ok'] and data['error']['code']=='profile_changed' and 'recordings' not in data
            data=m.engineer_request_data(True,{'profile':'profile','expected':1,'grant':'../private'})
            assert not data['ok'] and data['error']['code']=='invalid_reference'


def t_local_http_profile_origin_and_error_contract():
    import http.client
    from engineer_ui_fixture import Fixture,web_fixture
    with tempfile.TemporaryDirectory() as d:
        ui=Fixture(d);server,port=web_fixture._serve(ui.context())
        def request(method,path,body=None,origin=None):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            headers={'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            try:
                connection.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
                response=connection.getresponse();return response.status,json.loads(response.read())
            finally:connection.close()
        try:
            status,doc=request('GET','/api/engineer?profile=profile')
            assert status==200 and doc['ok'] and not doc['enabled']
            status,doc=request('POST','/api/engineer',{'profile':'profile','enabled':True,'expected':0},'https://foreign.example.test')
            assert status==403 and doc['error']['code']=='foreign_origin'
            assert not ui.access().read()['enabled']
            status,doc=request('GET','/api/engineer?profile=other')
            assert status==409 and doc['error']['code']=='profile_changed' and 'recordings' not in doc
            status,doc=request('POST','/api/engineer',{'profile':'profile','enabled':True,'expected':9})
            assert status==409 and doc['error']['code']=='conflict'
            status,doc=request('POST','/api/engineer',{'profile':'profile','grant':'../private','expected':0})
            assert status==400 and doc['error']['code']=='invalid_reference'
        finally:server.shutdown();server.server_close()


def t_engineer_page_resolves_in_source_flat_package_and_frozen_layouts():
    with tempfile.TemporaryDirectory() as d:
        for relative in ('src/console','package','frozen/console'):
            root=Path(d)/relative;root.mkdir(parents=True)
            console=root/'console.html';console.write_text('Console')
            engineer=root/'race-engineer.html';engineer.write_text('Engineer')
            assert fixture.m.engineer_page_for(str(console))==str(engineer)
            engineer.unlink();assert fixture.m.engineer_page_for(str(console)) is None
        assert fixture.m.engineer_page_for(None) is None


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

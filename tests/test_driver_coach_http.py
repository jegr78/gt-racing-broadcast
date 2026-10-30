#!/usr/bin/env python3
"""Real signed HTTP coaching controls preserve driver, producer and archive scopes."""
import json
import threading
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from test_driver_radio_ui import RadioFixture,console,features,switch


def answer(f,path='/console/driver-radio/coach',**kwargs):
    status,raw,headers=f.request(path,**kwargs)
    return status,json.loads(raw),headers


def t_signed_driver_can_start_steer_and_stop_only_its_own_training():
    with RadioFixture(coaching=True) as f:
        status,state,headers=answer(f)
        assert status==200 and not state['active'] and headers['Cache-Control']=='no-store'
        assert state['profile']=='solo-pov' and state['viewer']=='console:driver'
        status,pending,_=answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))
        assert status==200 and pending['active'] and pending['pending']
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        status,state,_=answer(f);assert status==200 and state['focus']['focus'] and state['latest']
        status,_,_=answer(f,method='POST',body=dict(expected=state['revision'],action='keep',options={}))
        assert status==200;f.practice.service.step()
        status,state,_=answer(f)
        status,stopped,_=answer(f,method='POST',body=dict(expected=state['revision'],action='stop',options={}))
        assert status==200 and not stopped['active']


def t_coach_switch_gates_every_signed_in_subject_and_foreign_writes_fail_closed():
    with RadioFixture(coaching=True) as f:
        for subject in ('director','unknown'):
            status,body,_=answer(f,subject=subject)
            assert status==200 and body['viewer']=='console:'+subject,(subject,status,body)
        status,body,_=answer(f,subject=None)
        assert status==401,(status,body)
        switch(f,engineer=True)
        for path in ('/console/driver-radio/coach','/driver-radio/admin/coach'):
            status,body,_=answer(f,path)
            assert status==403 and body['error']=={'code':'feature_disabled','message':'Enable the AI Coach for this profile'},(path,status,body)
        switch(f,engineer=True,coach=True)
        status,body,_=answer(f,method='POST',body={},headers={'Origin':'https://foreign.invalid'})
        assert status==403 and body['error']['code']=='foreign_origin'
        status,state,_=answer(f)
        bad=dict(expected=state['revision'],action='start',options={},owner={'profile':'solo-pov','subject':'local:producer'})
        status,body,_=answer(f,method='POST',body=bad)
        assert status==400 and body['error']['code']=='invalid_body'
        status,body,_=answer(f,method='POST',body=dict(expected=-1,action='start',options={}))
        assert status==409 and body['error']['code']=='conflict'
        assert not answer(f)[1]['active']


def t_local_producer_and_second_driver_cannot_impersonate_the_active_driver():
    with RadioFixture(coaching=True) as f:
        path='/driver-radio/admin/coach'
        status,state,_=answer(f,path);assert status==200 and state['viewer']=='local:producer'
        body=dict(profile='other',expected=state['revision'],action='start',options={})
        status,result,_=answer(f,path,method='POST',body=body)
        assert status==409 and result['error']['code']=='profile_changed'
        body['profile']='solo-pov'
        status,_,_=answer(f,path,method='POST',body=body,headers={'Origin':'https://foreign.invalid'})
        assert status==403
        status,_,_=answer(f,path,method='POST',body=body);assert status==200
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        status,busy,_=answer(f)
        assert status==200 and busy['phase']=='busy' and 'focus' not in busy and 'latest' not in busy
        status,result,_=answer(f,method='POST',body=dict(expected=busy['revision'],action='stop',options={}))
        assert status==409 and result['error']['code']=='training_busy'
        assert answer(f,path)[1]['active']


def t_live_reference_selection_is_server_resolved_and_driver_grants_do_not_open_archives():
    with RadioFixture(coaching=True) as f:
        state=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        state=answer(f)[1];assert state['references'] and state['references'][0]['source']=='personal'
        selected=state['references'][0]['id']
        status,_,_=answer(f,method='POST',body=dict(expected=state['revision'],action='reference',options={'lap_id':selected}))
        assert status==200;f.practice.service.step()
        state=answer(f)[1];assert state['calibration']['reference']['source']=='selected'
        actor=f.f.service.coach;before=actor.snapshot({'profile':'solo-pov','subject':'console:driver'})
        # Resolve a known archive selector through its real permission callback;
        # no archive file can be read without the separate Engineer grant.
        original=console.m.driver_coach_controls.References._load
        def load(self,ident):
            if ident.startswith('r:'):self.archive('a'*32)
            return original(self,ident)
        from unittest.mock import patch
        with patch.object(console.m.driver_coach_controls.References,'_load',load):
            status,result,_=answer(f,method='POST',body=dict(expected=state['revision'],action='reference',options={'lap_id':'r:'+('a'*32)+':1:2'}))
        assert status==403 and result['error']['code']=='forbidden'
        assert actor.snapshot({'profile':'solo-pov','subject':'console:driver'})==before


def t_stop_remains_available_after_the_control_rate_budget_is_exhausted():
    with RadioFixture(coaching=True) as f:
        state=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        errors=[]
        for _ in range(31):
            status,body,_=answer(f,method='POST',body=dict(expected=-1,action='objective',options={'objective':'pace'}))
            errors.append(body['error']['code']);assert status in (409,429)
        assert 'rate_limited' in errors
        state=answer(f)[1]
        status,stopped,_=answer(f,method='POST',body=dict(expected=state['revision'],action='stop',options={}))
        assert status==200 and not stopped['active'], 'Rate budget blocked an authorized Stop'


def t_stop_uses_current_permission_and_owner_without_requiring_the_latest_display_revision():
    with RadioFixture(coaching=True) as f:
        old=answer(f)[1]['revision']
        assert answer(f,method='POST',body=dict(expected=old,action='start',options={}))[0]==200
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        assert answer(f)[1]['revision']>old
        for invalid in (-1,True,0.0,None):
            status,body,_=answer(f,method='POST',body=dict(expected=invalid,action='stop',options={}))
            assert status==409 and body['error']['code']=='conflict'
            assert answer(f)[1]['active']
        status,stopped,_=answer(f,method='POST',body=dict(expected=old,action='stop',options={}))
        assert status==200 and not stopped['active']



def t_pending_post_and_get_never_return_old_focus_after_the_live_context_changes():
    with RadioFixture(coaching=True) as f:
        state=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        state=answer(f)[1];assert state['focus']['focus']
        f.practice.snap['current']['car_id']=99
        status,pending,_=answer(f,method='POST',body=dict(expected=state['revision'],action='skill',options={'skill':'braking'}))
        assert status==200 and pending['pending']
        assert pending['calibration'] is None and pending['focus'] is None and pending['latest'] is None
        current=answer(f)[1]
        assert current['pending'] and current['calibration'] is None and current['focus'] is None


def t_unreadable_local_settings_have_a_semantic_unavailable_response():
    with RadioFixture(coaching=True) as f:
        f.f.access.path.write_text('{broken',encoding='utf8')
        status,body,_=answer(f,'/driver-radio/admin/coach')
        assert status==503 and body['error']['code']=='settings_unavailable'


def t_control_center_proxy_reuses_local_scope_and_preserves_coach_revision_errors():
    with RadioFixture(coaching=True) as f:
        path='/api/driver-radio/coach'
        status,data,_=answer(f,path,cc=True)
        assert status==200 and data['ok'] and data['result']['viewer']=='local:producer'
        body=dict(expectedProfile='solo-pov',expected=data['result']['revision'],action='start',options={})
        status,data,_=answer(f,path,cc=True,method='POST',body=body)
        assert status==200 and data['result']['active']
        status,data,_=answer(f,path,cc=True,method='POST',body=body)
        assert status==409 and data['code']=='conflict'
        status,data,_=answer(f,path,cc=True,method='POST',body=body,headers={'Origin':'https://foreign.invalid'})
        assert status==403 and data['ok'] is False


def t_delayed_reference_choices_cannot_publish_old_context_or_revive_stopped_training():
    from unittest.mock import patch
    for change in ('context','stop'):
        with RadioFixture(coaching=True) as f:
            state=answer(f)[1]
            assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
            f.practice.service.step()
            for n in (2,3):f.practice.complete(n);f.practice.service.step()
            entered=threading.Event();release=threading.Event();replies=[]
            original=console.m.driver_coach_controls.References.choices
            def delayed(self,archives,entered=entered,release=release,original=original):
                choices=original(self,archives);entered.set();assert release.wait(4);return choices
            worker=None
            try:
                with patch.object(console.m.driver_coach_controls.References,'choices',delayed):
                    worker=threading.Thread(target=lambda f=f,replies=replies:replies.append(answer(f)));worker.start();assert entered.wait(1)
                    if change=='context':f.practice.snap['current']['car_id']=99
                    else:
                        state=f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})
                        status,stopped,_=answer(f,method='POST',body=dict(expected=state['revision'],action='stop',options={}))
                        assert status==200 and not stopped['active']
                    release.set();worker.join(5)
            finally:
                release.set()
                if worker is not None:worker.join(5)
            assert not worker.is_alive() and len(replies)==1
            status,state,_=replies[0]
            assert status==200 and state.get('focus') is None and state.get('latest') is None
            assert state['references']==[], 'Delayed old reference choices remained current'
            if change=='context':assert state['phase']=='paused'
            else:assert not state['active'], 'Delayed GET revived a stopped training'


def t_driver_cannot_select_an_unoffered_previous_session_live_reference():
    with RadioFixture(coaching=True) as f:
        state=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step();row=f.practice.complete(20,session_id='previous-driver-session')
        state=answer(f)[1];assert state['references']==[]
        before=f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})
        status,result,_=answer(f,method='POST',body=dict(expected=state['revision'],action='reference',options={'lap_id':row['id']}))
        assert status==410 and result['error']['code']=='reference_unavailable'
        assert f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})==before


def t_a_maximum_length_console_identity_can_train_without_truncation():
    with RadioFixture(coaching=True) as f:
        subject='x'*160
        status,state,_=answer(f,subject=subject);assert status==200
        status,state,_=answer(f,subject=subject,method='POST',body=dict(expected=state['revision'],action='start',options={}))
        assert status==200 and state['active'], 'A valid driver identity exceeded the namespaced owner budget'
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        status,state,_=answer(f,subject=subject)
        assert status==200 and state['focus']['focus'] and state['viewer']=='console:'+subject


def t_control_center_unreadable_settings_preserve_the_unavailable_error_status():
    with RadioFixture(coaching=True) as f:
        f.f.access.path.write_text('{broken',encoding='utf8')
        status,data,_=answer(f,'/api/driver-radio/coach',cc=True)
        assert status==503 and not data['ok'] and data['code']=='settings_unavailable'


def t_private_history_and_deletion_use_the_verified_driver_and_independent_history_revision():
    with RadioFixture(coaching=True) as f:
        state=answer(f)[1]
        assert state['history']['entries']==[]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        for n in (2,3,4):f.practice.complete(n);f.practice.service.step()
        state=answer(f)[1];history=state['history']
        assert history['entries'][0]['focus'] and history['entries'][0]['debrief']['observed']
        local=answer(f,'/driver-radio/admin/coach')[1]
        assert local['phase']=='busy' and local['history']['entries']==[]
        bad=dict(expected=history['revision'],action='delete_history',options={'id':None,'owner':'local:producer'})
        status,error,_=answer(f,method='POST',body=bad)
        assert status==400 and error['error']['code']=='invalid_body'
        good=dict(expected=history['revision'],action='delete_history',options={'id':None})
        status,result,_=answer(f,method='POST',body=good)
        assert status==200 and result['active'] and result['history']['entries']==[]
        f.practice.complete(5);f.practice.service.step()
        assert answer(f)[1]['history']['entries']==[],'Continued training restored deleted private history'


def t_history_io_does_not_delay_stop_and_cannot_reply_after_the_coach_is_switched_off():
    from unittest.mock import patch
    for change in ('stop','revoke','sign-out'):
        with RadioFixture(coaching=True) as f:
            state=answer(f)[1]
            assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
            f.practice.service.step()
            for n in (2,3):f.practice.complete(n);f.practice.service.step()
            entered=threading.Event();release=threading.Event();result=[]
            store=f.practice.service.journal;original=store.read
            def delayed(*args,original=original,entered=entered,release=release):
                value=original(*args);entered.set();assert release.wait(5);return value
            with patch.object(store,'read',delayed):
                thread=threading.Thread(target=lambda result=result,f=f:result.append(answer(f)));thread.start()
                try:
                    assert entered.wait(2)
                    if change=='stop':
                        status,stopped,_=answer(f,method='POST',body=dict(expected=0,action='stop',options={}))
                        assert status==200 and not stopped['active'],'History IO held up Stop acknowledgement'
                    elif change=='revoke':
                        switch(f,engineer=True)
                    else:  # The Console token is revoked while the reply is still being built.
                        console.m.console_admin.bump_version(str(Path(f.temp.name)/'console-versions.json'),'driver')
                finally:release.set();thread.join(5)
            assert not thread.is_alive() and result
            if change=='revoke':assert result[0][0]==403 and result[0][1]['error']['code']=='feature_disabled',result
            elif change=='sign-out':assert result[0][0]==403 and result[0][1]['error']['code']=='forbidden',result
            else:assert result[0][0]==200 and not result[0][1]['active']


def t_corrupt_history_does_not_disable_current_coaching_or_shared_radio():
    with RadioFixture(coaching=True) as f:
        state=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        path=next(f.practice.service.journal.directory.glob('*.json'));path.write_text('{broken',encoding='utf8')
        status,state,_=answer(f)
        assert status==200 and state['focus']['focus'] and state['history_error']=='history_unavailable'


def t_a_busy_local_producer_can_read_and_delete_its_past_history_without_touching_the_active_driver():
    with RadioFixture(coaching=True) as f:
        path='/driver-radio/admin/coach';local=answer(f,path)[1]
        body=dict(profile='solo-pov',expected=local['revision'],action='start',options={})
        assert answer(f,path,method='POST',body=body)[0]==200;f.practice.service.step()
        for n in (2,3,4):f.practice.complete(n);f.practice.service.step()
        body.update(expected=0,action='stop')
        assert answer(f,path,method='POST',body=body)[0]==200;f.practice.service.step()
        remote=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=remote['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        for n in (6,7,8):f.practice.complete(n);f.practice.service.step()
        owner={'profile':'solo-pov','subject':'console:driver'};before=f.practice.service.snapshot(owner)
        status,local,_=answer(f,path)
        assert status==200 and local['phase']=='busy' and local['history']['entries'][0]['status']=='stopped'
        assert 'focus' not in local and 'latest' not in local
        body=dict(profile='solo-pov',expected=local['history']['revision'],action='delete_history',options={'id':None})
        status,result,_=answer(f,path,method='POST',body=body)
        assert status==200 and result['history']['entries']==[]
        assert f.practice.service.snapshot(owner)==before


def t_historical_shared_reference_details_require_the_current_separate_engineer_role_and_grant():
    import copy
    from types import SimpleNamespace
    import test_ai_package as recording
    from test_driver_coach_service import real_lap
    for revoke in ('role','grant'):
        with RadioFixture(coaching=True) as f:
            source=recording.source(f.f.access.path.parent/'telemetry-recordings')
            f.engineer.update(enabled=True,grant=source.identity,expected=0)
            f.crew.inject_row(2,name='Driver',race_engineer=True)
            row=real_lap(20,8);row.update(id='r:'+source.identity+':1:20',session_id='recorded')
            def detail(_ident,grant,row=row,source=source):
                grant(source.identity);return dict(state='ready',lap=copy.deepcopy(row))
            f.telemetry.engineer.api=SimpleNamespace(_detail=detail,reference=lambda _ident,_path,row=row:dict(laps=[copy.deepcopy(row)]))
            state=answer(f)[1]
            assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
            f.practice.service.step()
            for n in (2,3):f.practice.complete(n);f.practice.service.step()
            state=answer(f)[1]
            assert answer(f,method='POST',body=dict(expected=state['revision'],action='reference',options={'lap_id':row['id']}))[0]==200
            f.practice.service.step()
            for n in (4,5):f.practice.complete(n);f.practice.service.step()
            entry=next(r for r in answer(f)[1]['history']['entries'] if r['reference'] and r['reference']['source']=='selected')
            assert entry['focus'] and entry['evidence_status']=='historical'
            if revoke=='role':f.crew.inject_row(2,name='Driver',race_engineer=False)
            else:f.engineer.update(revoke=source.identity,expected=f.engineer.read()['revision'])
            status,current,_=answer(f)
            assert status==200
            entry=next(r for r in current['history']['entries'] if r['id']==entry['id'])
            assert entry['evidence_status']=='reference_unavailable' and entry['reference'] is None and entry['focus'] is None
            assert entry['latest'].get('action') is None


def t_the_final_archive_check_cannot_leave_old_live_facts_or_a_switched_off_coach_in_the_reply():
    from types import SimpleNamespace
    from unittest.mock import patch
    import test_ai_package as recording
    for change in ('context','driver'):
        with RadioFixture(coaching=True) as f:
            source=recording.source(f.f.access.path.parent/'telemetry-recordings')
            f.engineer.update(enabled=True,grant=source.identity,expected=0)
            f.crew.inject_row(2,name='Driver',race_engineer=True)
            f.telemetry.engineer.api=SimpleNamespace(reference=lambda _id,_path:dict(laps=[]))
            state=answer(f)[1]
            assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
            f.practice.service.step()
            for n in (2,3,4):f.practice.complete(n);f.practice.service.step()
            original=f.engineer.reference;calls=[]
            def last_read(*args,original=original,calls=calls,change=change,f=f):
                result=original(*args);calls.append(args[0])
                if len(calls)==2:
                    if change=='context':f.practice.snap['current']['car_id']=99
                    else:
                        switch(f,engineer=True)
                return result
            with patch.object(f.engineer,'reference',last_read):status,result,_=answer(f)
            assert len(calls)==2
            if change=='context':assert status==200 and result['phase']=='paused' and result['focus'] is None
            else:assert status==403 and result['error']['code']=='feature_disabled',result


def t_the_actual_local_admin_client_can_read_all_supported_compact_history_entries():
    import copy
    import driver_radio_admin
    with RadioFixture(coaching=True) as f:
        path='/driver-radio/admin/coach';state=answer(f,path)[1]
        body=dict(profile='solo-pov',expected=state['revision'],action='start',options={})
        assert answer(f,path,method='POST',body=body)[0]==200;f.practice.service.step()
        for n in (2,3,4):f.practice.complete(n);f.practice.service.step()
        store=f.practice.service.journal;owner={'profile':'solo-pov','subject':'local:producer'}
        record=store.read(owner,f.practice.service._generation)['entries'][0]
        for n in range(32):
            value=copy.deepcopy(record);value.update(id=f'{n:032x}',training_id=f'{n:032x}',status='stopped',ended_at=f.practice.wall)
            assert store.save(owner,value)
        result=driver_radio_admin.Client('solo-pov',f.server.server_port).request(coach=True)
        assert len(result['history']['entries'])==32 and result['focus']['focus']


def t_stop_acknowledges_without_reading_unavailable_live_or_history_sources():
    from unittest.mock import patch
    with RadioFixture(coaching=True) as f:
        state=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        with patch.object(f.telemetry.engineer,'snapshot',side_effect=OSError('Synthetic unavailable source')):
            status,stopped,_=answer(f,method='POST',body=dict(expected=0,action='stop',options={}))
        assert status==200 and not stopped['active'] and stopped['pending']


def t_local_response_limits_reject_oversized_valid_json_for_both_radio_and_coaching():
    import io
    from unittest.mock import patch
    import driver_radio_admin
    base=json.dumps(dict(profile='solo-pov',protocol=driver_radio_admin.PROTOCOL)).encode()
    for coach,limit in ((False,65536),(True,driver_radio_admin.MAX_COACH_RESPONSE)):
        # The bounded read is still valid JSON; a decoder failure cannot hide a missing size guard.
        raw=base+b' '*limit
        with patch.object(driver_radio_admin.http_util,'open_url',return_value=io.BytesIO(raw)):
            try:driver_radio_admin.Client('solo-pov',8088).request(coach=coach)
            except ValueError:pass  # Both transports must fail closed on a response exceeding their bound.
            else:raise AssertionError('The local radio bridge accepted an oversized response')


def t_admin_errors_carry_the_relay_reason():
    import io
    from unittest.mock import patch
    import driver_radio_admin
    def answer(error):
        raw=json.dumps({'error':error}).encode()
        with patch.object(driver_radio_admin.http_util,'open_url',return_value=io.BytesIO(raw)):
            try:driver_radio_admin.Client('solo-pov',8088).request(coach=True)
            except driver_radio_admin.AdminError as exc:return exc.code,str(exc)
        raise AssertionError('an error answer must raise')
    reason='Enable the AI Coach for this profile'
    assert answer({'code':'feature_disabled','message':reason})==('feature_disabled',reason)
    named='Invalid radio limits or service selection'
    assert answer({'code':'invalid_settings','message':named})[1]==named, 'the relay names what is invalid'
    assert answer({'code':'feature_disabled'})[1]=='Enable the AI Race Engineer or the AI Coach for this profile first'
    assert answer({'code':'odd','message':'x'*400})[1]=='Radio administration unavailable', \
        'an oversized relay message is not shown'
    assert answer({'code':'odd','message':'line\nbreak'})[1]=='Radio administration unavailable'


def t_optional_ai_attachment_reaches_only_the_current_owner_review():
    import driver_radio_providers as providers
    class AI:
        def explain(self,*args):
            return dict(explanation=providers.COACH_EXPLANATIONS[1],evidence=['coach.lap_time_s'],claims=[],exercise='repeat')
    with RadioFixture(coaching=True) as f:
        config=f.f.access.read();f.f.access.update(dict(expected=config['revision'],features=features(True,True,'fixture'),providers=dict(speech=False)))
        state=answer(f)[1]
        assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={'allow_ai':True}))[0]==200
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        f.enricher.ai=AI();assert f.enricher.step('ai')
        message=f.f.service.history(requester='driver')['messages'][-1]
        state=answer(f)[1]
        assert state['latest'].get('explanation')==message['explanation'],'Validated optional AI vanished before the driver review'
        assert state['latest']['ai_state']=='ready'
        foreign=answer(f,subject='other')[1]
        assert foreign['phase']=='busy' and 'latest' not in foreign and message['explanation'] not in json.dumps(foreign)
        f.practice.complete(4);f.practice.service.step()
        assert not answer(f)[1]['latest'].get('explanation'),'Old lap AI survived a new completed-lap review'


def t_archive_context_correction_during_final_grant_io_redacts_old_history():
    import copy
    from types import SimpleNamespace
    from unittest.mock import patch
    import test_ai_package as recording
    from test_driver_coach_service import real_lap
    import gt7_context
    with RadioFixture(coaching=True) as f:
        source=recording.source(f.f.access.path.parent/'telemetry-recordings')
        f.engineer.update(enabled=True,grant=source.identity,expected=0)
        f.crew.inject_row(2,name='Driver',race_engineer=True)
        row=real_lap(20,8);row.update(id='r:'+source.identity+':1:20',session_id='recorded')
        def detail(_ident,grant):
            grant(source.identity);return dict(state='ready',lap=copy.deepcopy(row))
        f.telemetry.engineer.api=SimpleNamespace(_detail=detail,reference=lambda *args:dict(laps=[copy.deepcopy(row)]))
        state=answer(f)[1];assert answer(f,method='POST',body=dict(expected=state['revision'],action='start',options={}))[0]==200
        f.practice.service.step()
        for n in (2,3):f.practice.complete(n);f.practice.service.step()
        state=answer(f)[1];assert answer(f,method='POST',body=dict(expected=state['revision'],action='reference',options={'lap_id':row['id']}))[0]==200
        f.practice.service.step()
        for n in (4,5):f.practice.complete(n);f.practice.service.step()
        state=answer(f)[1];assert answer(f,method='POST',body=dict(expected=state['revision'],action='stop',options={}))[0]==200
        f.practice.service.step()
        before=answer(f)[1];entry=next(r for r in before['history']['entries'] if r['reference'] and r['reference']['source']=='selected')
        assert entry['focus'] and entry['evidence_status']=='historical'
        original=f.engineer.reference;primed=[];changed=[]
        original_prepare=console.m.driver_coach_calibration._prepare
        def prepare(value):
            result=original_prepare(value)
            if value.get('id')==row['id']:primed.append(True)
            return result
        def final_grant(*args):
            path=original(*args)
            if primed and not changed:
                Path(gt7_context._stem(str(path))+'.context.json').write_text('{"corrected":true}',encoding='utf8')
                row['measurement_hash']='corrected-during-final-archive-io';changed.append(True)
            return path
        with patch.object(console.m.driver_coach_calibration,'_prepare',prepare),patch.object(f.engineer,'reference',final_grant):
            status,current,_=answer(f)
        assert status==200 and changed
        result=next(r for r in current['history']['entries'] if r['id']==entry['id'])
        assert result['reference'] is None and result['focus'] is None,'Corrected archive kept the cached historical focus'
        assert result['evidence_status']=='reference_unavailable'


if __name__=='__main__':
    tests=[(name,fn) for name,fn in globals().copy().items() if name.startswith('t_') and callable(fn)]
    for name,fn in tests:fn();print('PASS',name)
    print('PASS',len(tests),'signed coach HTTP scenarios')

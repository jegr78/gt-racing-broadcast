#!/usr/bin/env python3
"""Real signed HTTP coaching controls preserve driver, producer and archive scopes."""
import json
import threading
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from test_driver_radio_ui import RadioFixture,console


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


def t_roles_and_driver_assignment_remain_separate_and_foreign_writes_fail_closed():
    with RadioFixture(coaching=True) as f:
        for subject in ('director','unknown'):
            status,body,_=answer(f,subject=subject)
            assert status==403 and body['error']['code']=='forbidden'
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


if __name__=='__main__':
    tests=[(name,fn) for name,fn in globals().copy().items() if name.startswith('t_') and callable(fn)]
    for name,fn in tests:fn();print('PASS',name)
    print('PASS',len(tests),'signed coach HTTP scenarios')

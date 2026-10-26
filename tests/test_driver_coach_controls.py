#!/usr/bin/env python3
"""Bounded coaching commands, explicit native vocabulary and private grounded replies."""
import copy
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src/scripts'),str(Path(__file__).parent)]
import driver_coach_controls as controls
from test_driver_coach_service import Fixture,LOCAL,REMOTE,rejects,real_lap


def t_http_commands_have_exact_shapes_and_no_client_selected_identity_or_raw_reference():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root)
        for body in ({},dict(expected=0,action='start',options={},owner=LOCAL),
                     dict(expected=0,action='reference',options={'row':real_lap(2,8)}),
                     dict(expected=0,action='start',options=[],profile='other')):
            rejects(lambda body=body:controls.command(f.service,LOCAL,body,lambda:True),'invalid_body')
        state=controls.command(f.service,LOCAL,dict(expected=0,action='start',options={}),lambda:True)
        assert state['active'] and state['pending']
        rejects(lambda:controls.command(f.service,LOCAL,dict(expected=0,action='stop',options={}),lambda:True),'conflict')


def t_selected_reference_is_resolved_by_trusted_server_code_and_keeps_its_grant():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        calls=[];allowed=[True];row=real_lap(20,8)
        def resolve(ident):calls.append(ident);return copy.deepcopy(row),lambda:allowed[0]
        body=dict(expected=f.service.snapshot(LOCAL)['revision'],action='reference',options={'lap_id':'trusted-lap'})
        controls.command(f.service,LOCAL,body,lambda:True,resolve=resolve);state=f.step()
        assert calls==['trusted-lap'] and state['calibration']['reference']['source']=='selected'
        allowed[0]=False;state=f.step()
        assert not state['focus']['focus'] and state['calibration']['reference']['source']=='personal'
        body.update(expected=state['revision'],options={'lap_id':None})
        controls.command(f.service,LOCAL,body,lambda:True);f.step()


def t_native_vocabulary_is_exact_bounded_and_does_not_guess_unsupported_actions():
    assert controls.voice_plan('Hey Jeff, start training!')==dict(kind='command',action='start',options={})
    assert controls.voice_plan('Training stoppen')==dict(kind='command',action='stop',options={})
    assert controls.voice_plan('Übung überspringen')==dict(kind='command',action='skip',options={})
    assert controls.voice_plan('objective pace')['options']=={'objective':'pace'}
    assert controls.voice_plan('Fokus Bremsen')['options']=={'skill':'braking'}
    assert controls.voice_plan('training status')==dict(kind='question',topic='status')
    for text in ('start training and stop training','coach change the throttle target','focus on secrets'):
        assert controls.voice_plan(text)==dict(kind='clarify')
    assert controls.voice_plan('Fuel after another lap?') is None
    for text in ('x'*401,'training\nstart',None):rejects(lambda text=text:controls.voice_plan(text),'invalid_body')


def t_native_controls_are_local_only_and_never_read_another_driver_focus():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command(owner=REMOTE);f.step()
        for n in (2,3):f.complete(n);f.step()
        before=f.service.snapshot(REMOTE)
        reply=controls.voice(f.service,'stop training',lambda:True)
        assert 'another' in reply.lower() and f.service.snapshot(REMOTE)==before
        reply=controls.voice(f.service,'current focus',lambda:True)
        assert 'another' in reply.lower() and before['focus']['focus']['action'] not in reply
        assert controls.voice(f.service,'fuel status',lambda:True) is None


def t_voice_controls_do_not_enable_ai_and_questions_use_only_retained_current_evidence():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root)
        assert 'accepted' in controls.voice(f.service,'start training',lambda:True).lower()
        f.step();assert not f.service.snapshot(LOCAL)['allow_ai']
        for n in (2,3):f.complete(n);f.step()
        state=f.service.snapshot(LOCAL)
        reply=controls.voice(f.service,'current focus',lambda:True)
        assert state['focus']['focus']['action'] in reply
        reply=controls.voice(f.service,'training evidence',lambda:True)
        assert state['latest']['observation'] in reply and state['latest']['check'] in reply
        controls.voice(f.service,'keep focus',lambda:True);f.step()
        assert f.service.snapshot(LOCAL)['focus']['focus']==state['focus']['focus']
        before=f.service.snapshot(LOCAL)
        controls.voice(f.service,'coach invent another exercise',lambda:True)
        assert f.service.snapshot(LOCAL)==before
        rejects(lambda:controls.voice(f.service,'stop training',lambda:False),'forbidden')


def t_voice_and_driver_views_hide_corrected_or_stale_evidence_before_the_cpu_worker_runs():
    for change in ('correction','stale','context'):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.command();f.step()
            for n in (2,3):f.complete(n);f.step()
            before=f.service.snapshot(LOCAL);assert before['latest'] and before['focus']['focus']
            if change=='correction':f.snap['laps'][-1]['measurement_hash']='corrected'
            elif change=='stale':f.snap['receipt']['state']='stale'
            else:f.snap['current']['car_id']=99
            state=f.service.view(LOCAL)
            assert state['phase']=='paused' and state['latest'] is None and state['focus'] is None
            assert 'unavailable' in controls.voice(f.service,'training evidence',lambda:True)
            assert f.service.snapshot(LOCAL)==before, 'A reader mutated the worker model'


def t_reference_lookup_cannot_run_for_a_stale_control_or_ungranted_driver():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();calls=[]
        def resolve(ident):calls.append(ident);raise AssertionError('Unexpected reference lookup')
        body=dict(expected=-1,action='reference',options={'lap_id':'foreign'})
        rejects(lambda:controls.command(f.service,LOCAL,body,lambda:True,resolve=resolve),'conflict')
        body['expected']=f.service.snapshot(LOCAL)['revision']
        rejects(lambda:controls.command(f.service,LOCAL,body,lambda:False,resolve=resolve),'forbidden')
        assert not calls


def t_archive_reference_guard_rechecks_grants_measurements_and_live_context():
    for change in ('grant','measurement','definition','quality','context','stale'):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);row=real_lap(20,8);row['id']='r:'+('a'*32)+':1:20';row['session_id']='recorded'
            allowed=[True]
            def archive(_ident,allowed=allowed):
                if not allowed[0]:raise controls.calibration.CoachError('reference_unavailable')
                return Path(root)/'granted.gt7rec'
            def detail(ident,grant,row=row):grant('a'*32);return dict(state='ready',lap=copy.deepcopy(row))
            live=SimpleNamespace(snapshot=lambda f=f:copy.deepcopy(f.snap),detail=f.detail,
                                 api=SimpleNamespace(_detail=detail))
            frozen,grant=controls.References(live,archive).resolve(row['id'])
            assert frozen['id']==row['id'] and controls.orchestration._allowed(grant)
            if change=='grant':allowed[0]=False
            elif change=='measurement':row['measurement_hash']='changed'
            elif change=='definition':row['definition_fingerprint']='changed'
            elif change=='quality':row['trace_complete']=False
            elif change=='context':f.snap['current']['car_id']=2
            else:f.snap['receipt']['state']='stale'
            assert not controls.orchestration._allowed(grant),change


def t_reference_choices_exclude_incompatible_live_laps_and_are_bounded():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root)
        for n in range(2,30):f.complete(n)
        f.snap['laps'][-1]['car_id']=99;f.rows['lap-29']['car_id']=99
        live=SimpleNamespace(snapshot=lambda:copy.deepcopy(f.snap),detail=f.detail)
        references=controls.References(live,lambda _source:None)
        choices=references.choices([])
        assert len(choices)<=16 and all(r['id']!='lap-29' for r in choices)
        rejects(lambda:references.resolve('lap-29'),'incompatible_reference')


def t_unoffered_previous_session_live_rows_cannot_bypass_the_archive_boundary():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);row=f.complete(20,session_id='previous-driver-session')
        live=SimpleNamespace(snapshot=lambda:copy.deepcopy(f.snap),detail=f.detail)
        references=controls.References(live,lambda _source:None)
        assert references.choices([])==[]
        rejects(lambda:references.resolve(row['id']),'reference_unavailable')


def t_actual_native_worker_routes_coaching_without_a_browser_or_a_second_audio_owner():
    import driver_radio_native as native
    from test_driver_radio_native import Worker,Player
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(Path(root)/'solo');f.radio.service.coach=f.service;workers=[]
        def worker(_body):w=Worker();workers.append(w);return w
        owner=native.Supervisor(f.radio.service,Path(root)/'machine',worker=worker,player=lambda *_args:Player(),
                               microphone=lambda:'Synthetic microphone',ready=lambda:True,clock=lambda:f.at)
        try:
            owner.step();assert len(workers)==1
            workers[-1].events=[dict(event='question',text='start training')]
            owner.step();assert f.service.snapshot(LOCAL)['active'], 'Native coaching request was routed as an ordinary telemetry question'
            f.service.step();assert not f.service.snapshot(LOCAL)['allow_ai']
            for n in (2,3):f.complete(n);f.service.step()
            state=f.service.view(LOCAL);assert state['focus']['focus']
            # Isolate the next captured utterance from automatic lap feedback.
            f.radio.service.cancel_coach();f.at+=1;f.wall+=1;owner.step()
            assert owner._worker_proc is not None
            workers[-1].events=[dict(event='question',text='current focus')]
            owner.step()
            assert state['focus']['focus']['action'] in f.messages()[-1]['text']
            assert f.messages()[-1]['ai_state']=='not_requested' and not f.radio.service._conversation
        finally:owner.close()


if __name__=='__main__':
    tests=[(name,fn) for name,fn in globals().copy().items() if name.startswith('t_') and callable(fn)]
    for name,fn in tests:fn();print('PASS',name)
    print('PASS',len(tests),'coach control scenarios')

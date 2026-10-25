#!/usr/bin/env python3
"""Explicit live practice, current evidence and shared private radio scheduling."""
import copy
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_coach_service as coach
import driver_coach_calibration as calibration
from test_driver_coach_focus import real_lap,real_definition
from test_driver_radio_service import Fixture as RadioFixture

LOCAL={'profile':'solo','subject':'local:producer'}
REMOTE={'profile':'solo','subject':'console:driver'}


class Fixture:
    def __init__(self,root,assessor=None):
        self.radio=RadioFixture(root)
        self.at=100.0;self.wall=1000.0;self.granted=True;self.rows={}
        self.radio.service.clock=lambda:self.at
        self.radio.service.wall=lambda:self.wall
        self.snap=dict(generation='generation',session_id='live',context_revision=0,revision=1,
                       current=self.current(1),laps=[],receipt=dict(state='live',received_at=self.wall),
                       replay=dict(active=False),instruments=dict(speed_kmh=100,brake=0,throttle=100,steer_rad=0))
        self.radio.snap=self.snap
        self.service=coach.Service('solo',lambda:copy.deepcopy(self.snap),self.detail,self.radio.service,
                                   clock=lambda:self.at,wall=lambda:self.wall,assessor=assessor)
    def current(self,n):
        row=real_lap(n,8);row.update(state='provisional',comparison_eligible=False,_start_wall=self.wall)
        return row
    def detail(self,ident):return {'state':'ready','lap':copy.deepcopy(self.rows.get(ident))}
    def command(self,action='start',owner=LOCAL,**options):
        return self.service.command(owner,action,self.service.snapshot(owner)['revision'],options,
                                    authorize=lambda:self.granted)
    def step(self):self.service.step();return self.service.snapshot(LOCAL)
    def complete(self,n,seconds=8,**changes):
        self.at+=10;self.wall+=10
        row=real_lap(n,seconds);row.update(_start_wall=self.wall-8,_end_wall=self.wall,
                                         measurement_hash='hash-'+str(n),track_definition=real_definition())
        row.update(changes);self.rows[row['id']]=copy.deepcopy(row)
        summary={k:v for k,v in row.items() if k not in ('trace','track_definition')}
        self.snap['laps'].append(summary);self.snap['current']=self.current(n+1)
        self.snap['receipt']['received_at']=self.wall;self.snap['revision']+=1
        return row
    def messages(self,requester=None):
        return [m for m in self.radio.service.history(requester=requester)['messages'] if m['kind']=='coach']


def rejects(fn,code):
    try:fn()
    except calibration.CoachError as error:assert error.code==code,(error.code,code)
    else:raise AssertionError('Expected coach error '+code)


def t_explicit_start_watermark_excludes_old_history_and_partial_current_lap():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.complete(1);f.complete(2)
        assert not f.step()['active'] and not f.messages()
        start=f.command();assert start['active'] and start['pending']
        state=f.step();assert state['calibration']['calibration_ids']==[]
        f.complete(3);state=f.step()
        assert state['calibration']['calibration_ids']==[], 'Partly driven start lap counted'
        f.complete(4);f.step();f.complete(5,8.2);state=f.step()
        assert state['calibration']['phase']=='provisional' and state['focus']['focus']
        f.complete(6,7.9);state=f.step()
        assert state['calibration']['phase']=='ready' and state['latest']['lap_id']=='lap-6'
        assert f.messages() and all(m['ai_state']=='not_requested' for m in f.messages())


def t_current_private_text_is_visible_only_to_its_owner_and_native_output():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command(owner=REMOTE);f.step()
        for n in (2,3,4):f.complete(n);f.step()
        own=f.service.snapshot(REMOTE);foreign=f.service.snapshot(LOCAL)
        assert own['focus']['focus'] and foreign['phase']=='busy'
        assert 'focus' not in foreign and 'latest' not in foreign
        assert f.messages('driver') and not f.messages('another-driver')
        assert f.messages() and all('_coach_owner' not in m for m in f.messages())
        rejects(lambda:f.command('stop',owner=LOCAL),'training_busy')


def t_stale_replay_context_and_revoked_grants_cannot_publish_old_advice():
    for change in ('stale','replay','context','grant'):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.command();f.step()
            f.complete(2);f.step();f.complete(3);f.step();before=len(f.messages())
            f.complete(4)
            if change=='stale':f.snap['receipt']['state']='stale'
            if change=='replay':f.snap['replay']['active']=True
            if change=='context':f.snap['current']['car_id']=2
            if change=='grant':f.granted=False
            state=f.step()
            assert len(f.messages())==before,change
            assert not state.get('latest') or state['latest']['lap_id']!='lap-4'
            assert not f.radio.service.take_playback()


def t_stop_remains_responsive_and_discards_a_calculation_already_in_flight():
    entered=threading.Event();release=threading.Event()
    import driver_coach_assessment
    def blocked(*args,**kwargs):
        entered.set();assert release.wait(5);return driver_coach_assessment.assess(*args,**kwargs)
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root,assessor=blocked);f.command();f.step();f.complete(2)
        worker=threading.Thread(target=f.service.step);worker.start()
        assert entered.wait(2)
        state=f.command('stop');assert not state['active'] and not state.get('latest')
        release.set();worker.join(5);assert not worker.is_alive()
        f.step();assert not f.messages() and not f.service.snapshot(LOCAL)['active']


def t_unsuitable_lap_retains_focus_without_an_attempt_score_and_commands_are_scoped():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        before=f.service.snapshot(LOCAL)['focus']
        f.complete(4,analysis_role='warmup');state=f.step()
        assert state['focus']['focus']==before['focus'] and state['focus']['attempts']==before['attempts']
        assert 'unsuitable' in state['latest']['text'].lower()
        rejects(lambda:f.service.command(LOCAL,'skip',-1,{},authorize=lambda:True),'conflict')
        rejects(lambda:f.service.command(LOCAL,'skip',state['revision'],{},authorize=lambda:False),'forbidden')
        rejects(lambda:f.command('invented'),'invalid_control')


def t_text_evaluation_continues_after_the_per_step_budget_and_metadata_is_not_a_new_lap():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in range(2,11):f.complete(n);f.step()
        state=f.service.snapshot(LOCAL)
        assert state['latest']['lap_id']=='lap-10'
        count=len(f.messages())
        for brief in f.snap['laps']:brief['context_revision']=1
        for row in f.rows.values():row['context_revision']=1
        f.snap['context_revision']=1
        f.step();assert len(f.messages())==count


def t_high_workload_blocks_coach_playback_even_if_the_cpu_worker_has_not_run_again():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();f.complete(2);f.step()
        f.at+=2
        assert f.messages()
        f.snap['instruments']['brake']=80
        assert f.radio.service.take_playback() is None


def t_reference_revocation_without_a_new_lap_cancels_focus_and_pending_output():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        allowed=[True];row=real_lap(20,8.5)
        f.command('reference',row=row,authorize=lambda:allowed[0]);f.step()
        for n in (5,6):f.complete(n);f.step()
        assert f.service.snapshot(LOCAL)['focus']['focus']
        allowed[0]=False
        state=f.step()
        assert state['focus']['focus'] is None and state['latest'] is None
        assert not f.radio.service.take_playback()


def t_old_completion_after_reconnect_keeps_text_without_a_new_speech_or_ai_deadline():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command(allow_ai=True);f.step()
        for n in (2,3):f.complete(n);f.step()
        f.snap['receipt']['state']='stale';f.complete(4);f.step()
        f.at+=120;f.wall+=120;f.snap['receipt'].update(state='live',received_at=f.wall)
        state=f.step();assert state['latest']['lap_id']=='lap-4'
        f.at+=2;f.wall+=2
        assert f.radio.service.take_playback() is None, 'Old completed lap received a fresh spoken deadline'
        last=f.messages()[-1]
        assert last['state']=='text_only' and last['ai_state']=='not_requested'
        assert f.radio.service.remaining_for(last['id'],last['epoch'],kind='ai')==0


def t_corrected_latest_baseline_and_working_reference_cancel_output_without_a_cpu_step():
    for changed in (7,2,4):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.command(objective='pace');f.step()
            for n,seconds in ((2,8.2),(3,8),(4,7.8),(5,7.8),(6,7.8),(7,8.1)):
                f.complete(n,seconds);f.step()
            state=f.service.snapshot(LOCAL)
            assert state['calibration']['reference']['id']=='lap-4'
            for brief in f.snap['laps']:
                if brief['id']=='lap-'+str(changed):brief.update(measurement_hash='corrected',time_s=9)
            f.rows['lap-'+str(changed)].update(measurement_hash='corrected',time_s=9)
            f.at+=2;f.wall+=2
            assert f.radio.service.take_playback() is None,changed
            last=f.messages()[-1]
            assert not f.radio.service.attach(last['id'],last['epoch'],{'explanation':'Old evidence','ai_state':'ready'})


def t_deferred_coach_speech_does_not_recreate_the_native_microphone_owner():
    import driver_radio_native as native
    from test_driver_radio_native import Worker,Player
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();f.complete(2);f.step()
        workers=[];players=[]
        def worker(_config):w=Worker();workers.append(w);return w
        def player(_audio,_volume):p=Player();players.append(p);return p
        owner=native.Supervisor(f.radio.service,Path(root)/'machine',worker=worker,player=player,
                               microphone=lambda:'Fixture microphone',clock=lambda:f.at,ready=lambda:True)
        try:
            owner.step();assert len(workers)==1
            f.complete(3);f.step()
            for _ in range(6):owner.step()
            assert len(workers)==1 and not workers[0].closed and not players
            f.at+=2;f.wall+=2
            message=f.messages()[-1]
            assert f.radio.service.attach(message['id'],message['epoch'],{'audio':b'fixture','audio_state':'ready'})
            owner.step();assert workers[0].closed and len(players)==1
        finally:owner.close()


def t_revoked_commands_cannot_mutate_focus_after_queueing_or_a_delayed_source_read():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        before=f.service.snapshot(LOCAL)['focus']
        f.command('skip');f.granted=False
        state=f.step()
        assert f.service._model is None or f.service._model['focus'].snapshot()==before
        assert not state['active'] and state['phase']=='forbidden'
    entered=threading.Event();release=threading.Event()
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);errors=[]
        def source():entered.set();assert release.wait(3);return copy.deepcopy(f.snap)
        f.service.source=source
        def start():
            try:f.command()
            except calibration.CoachError as error:errors.append(error.code)
        thread=threading.Thread(target=start);thread.start();assert entered.wait(2)
        f.granted=False;release.set();thread.join(3)
        assert not thread.is_alive() and errors==['forbidden']
        assert not f.service.snapshot(LOCAL)['active']


def t_invalid_control_shapes_are_rejected_before_a_command_is_accepted():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();before=f.service.snapshot(LOCAL)
        for action,options in (('objective',{}),('reference',{}),('objective',[]),([],{})):
            rejects(lambda action=action,options=options:f.service.command(LOCAL,action,before['revision'],options,
                    authorize=lambda:True),'invalid_control' if isinstance(action,list) else 'invalid_body')
        assert f.service.snapshot(LOCAL)==before


def t_cached_phase_and_pending_changes_advance_the_optimistic_revision():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);pending=f.command();active=f.step()
        assert active['revision']>pending['revision'] and not active['pending']
        f.snap['receipt']['state']='stale';paused=f.step()
        assert paused['revision']>active['revision'] and paused['phase']=='paused'
        same=f.step();assert same['revision']==paused['revision']
        pending=f.command('stop');stopped=f.step()
        assert stopped['revision']>pending['revision'] and not stopped['pending']


if __name__=='__main__':
    tests=[(n,f) for n,f in globals().copy().items() if n.startswith('t_') and callable(f)]
    for n,f in tests:f();print('PASS',n)
    print('PASS',len(tests),'live coach scenarios')

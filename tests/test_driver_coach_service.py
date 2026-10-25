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


if __name__=='__main__':
    tests=[(n,f) for n,f in globals().copy().items() if n.startswith('t_') and callable(f)]
    for n,f in tests:f();print('PASS',n)
    print('PASS',len(tests),'live coach scenarios')

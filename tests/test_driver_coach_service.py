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
import driver_radio_providers as providers
COACH_EXPLANATIONS=providers.COACH_EXPLANATIONS
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
                               microphone=lambda:'Fixture microphone',clock=lambda f=f:f.at,ready=lambda:True)
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


def t_real_live_store_packets_drive_fresh_calibration_and_focus_without_an_external_target():
    from test_gt7_laps import FakeCars,FakeTracks,write_circle_recording
    import gt7_recording,gt7_telemetry
    from race_engineer_live import Store
    with tempfile.TemporaryDirectory() as root:
        path=write_circle_recording(root,lap_secs=(20,20,16,20,19,18))
        packets=list(gt7_recording.Recording(path).packets());clock=[100.0];wall=[packets[0][0]]
        with Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),queue_max=4096,clock=lambda:clock[0]) as store:
            radio=RadioFixture(root);radio.service.source=store.snapshot
            radio.service.clock=lambda:clock[0];radio.service.wall=lambda:wall[0]
            service=coach.Service('solo',store.snapshot,store.detail,radio.service,clock=lambda:clock[0],wall=lambda:wall[0])
            try:
                for i,(stamp,_kind,plain) in enumerate(packets):
                    wall[0]=stamp;clock[0]=100+stamp-packets[0][0]
                    store.put(gt7_telemetry.parse_packet(plain),stamp)
                    if i==9:
                        assert store.flush(10)
                        service.command(LOCAL,'start',0,{},authorize=lambda:True);service.step()
                    elif i%400==399:
                        assert store.flush(10);service.step()
                assert store.flush(10);service.step()
                state=service.snapshot(LOCAL)
                assert state['calibration']['phase']=='ready',state['calibration']
                assert state['focus']['focus'] and state['focus']['focus']['skill']=='consistency'
                baseline=state['calibration']['baseline']['lap_ids']
                lookup={r['id']:r for r in store.snapshot()['laps']}
                assert all(lookup[lid]['lap']>=3 for lid in baseline), 'Old or partly driven context lap calibrated'
                assert state['latest'] and state['latest']['lap_id'] in lookup
                assert any(m['kind']=='coach' for m in radio.service.history()['messages'])
            finally:service.close();radio.service.close()


def t_review_uses_measured_lap_time_and_never_promotes_an_unselected_second_action():
    from test_driver_coach_focus import finding
    import driver_coach_assessment
    def assess(row,reference,**kwargs):
        result=driver_coach_assessment.assess(row,reference,**kwargs)
        chosen=finding('chosen','braking');chosen['action']='Consider the retained brake experiment.'
        other=finding('other','steering',8,3);other['action']='Consider a different steering experiment.'
        result['findings']=[chosen] if row['lap']<=3 else [other,chosen]
        for fact in result['findings']:
            for evidence in fact['evidence']:evidence['lap_id']=row['id']
        result['status']='ready';result['summary']=result['findings'][0]['action']
        return result
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root,assessor=assess);f.command();f.step()
        for n in (2,3,4):f.complete(n,8.5);f.step()
        state=f.service.snapshot(LOCAL);text=state['latest']['text']+' '+state['latest']['summary']
        assert state['focus']['focus']['id']=='chosen'
        assert 'different steering' not in text, 'Unselected assessment suggestion became a second coaching action'
        assert '8.5' in state['latest']['text'] and 'retained brake' in text
        assert all(state['latest'][k] for k in ('observation','interpretation','action','check'))


def t_optional_ai_requires_training_consent_and_uses_guarded_current_progress_only():
    class AI:
        def __init__(self):self.calls=[]
        def explain(self,agent,message,stopped,budget):
            self.calls.append((agent,message,budget))
            return dict(explanation=COACH_EXPLANATIONS[1],
                        evidence=['coach.lap_time_s'],claims=[],exercise='repeat')
    for consent in (False,True):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.radio.access.update({'expected':1,'providers':{'agent':'fixture','speech':False}})
            ai=AI();worker=providers.Enricher(f.radio.service,ai=ai,clock=lambda f=f:f.at)
            f.command(allow_ai=consent);f.step()
            for n in (2,3):f.complete(n);f.step()
            result=worker.step('ai')
            assert bool(ai.calls)==consent
            if consent:
                assert result and ai.calls[0][1]['evidence']['coach_context']['variants']
                assert 'history' not in ai.calls[0][1]['evidence']
                assert 'trace' not in str(ai.calls[0][1]['evidence'])
                assert f.messages()[-1]['ai_state']=='ready'
                assert f.messages()[-1]['text']!=f.messages()[-1]['explanation']


def t_ai_progress_claims_bind_all_recent_attempts_not_only_the_last_lap():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command(objective='pace',allow_ai=True);f.step()
        for n,seconds in ((2,8.2),(3,8),(4,7.8),(5,7.8),(6,7.8)):
            f.complete(n,seconds);f.step()
        message=f.messages()[-1]
        assert message['evidence']['coach']['sample_count']==3
        for brief in f.snap['laps']:
            if brief['id']=='lap-4':brief['measurement_hash']='corrected-earlier-attempt'
        f.rows['lap-4']['measurement_hash']='corrected-earlier-attempt'
        assert f.radio.service.remaining_for(message['id'],message['epoch'],kind='ai')==0
        assert not f.radio.service.attach(message['id'],message['epoch'],{'explanation':'Stale progress','ai_state':'ready'})


def t_invalid_or_late_ai_keeps_grounded_text_and_cannot_change_the_focus():
    bad=[dict(explanation='The observations prove learning.',exercise='current'),
         dict(explanation='You could brake two metres later.',exercise='current'),
         dict(explanation='The result is faster.',exercise='current'),
         dict(explanation='The result may be provisional.',exercise='invented')]
    for change in bad:
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.radio.access.update({'expected':1,'providers':{'agent':'fixture','speech':False}})
            f.command(allow_ai=True);f.step()
            for n in (2,3):f.complete(n);f.step()
            message=f.messages()[-1];before=f.service.snapshot(LOCAL)['focus']['focus']
            class AI:
                def explain(self,*_args,change=change):return dict(evidence=['coach.lap_time_s'],claims=[],**change)
            worker=providers.Enricher(f.radio.service,ai=AI(),clock=lambda f=f:f.at)
            assert worker.step('ai')
            last=f.messages()[-1]
            assert last['ai_state']=='failed' and last['text']==message['text'] and 'explanation' not in last
            assert f.service.snapshot(LOCAL)['focus']['focus']==before
    for stale in ('deadline','focus'):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.radio.access.update({'expected':1,'providers':{'agent':'fixture','speech':False}})
            f.command(allow_ai=True);f.step()
            for n in (2,3):f.complete(n);f.step()
            original=f.messages()[-1]['text']
            class Late:
                def explain(self,*_args,stale=stale,f=f):
                    if stale=='deadline':f.at+=13;f.wall+=13
                    else:f.command('skip')
                    return dict(explanation=COACH_EXPLANATIONS[0],evidence=['coach.lap_time_s'],claims=[],exercise='current')
            worker=providers.Enricher(f.radio.service,ai=Late(),clock=lambda f=f:f.at)
            assert not worker.step('ai')
            assert all('explanation' not in m for m in f.messages()) and f.messages()[-1]['text']==original


def t_coach_cli_package_contains_only_current_bounded_context_and_a_variant_schema():
    import ai_agents,driver_radio_ai
    from test_driver_radio_ai import Adapter,config
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command(allow_ai=True);f.step()
        for n in (2,3):f.complete(n);f.step()
        machine=Path(root)/'machine';ai_agents.Settings(machine).save({'enabled':True,'agents':[config()]})
        seen=[]
        def process(invocation,_prompt,_stopped,budget,env):
            import json
            data=json.loads((Path(invocation.cwd)/'facts.json').read_text())
            schema=json.loads((Path(invocation.cwd)/'result-schema.json').read_text())
            seen.append(data)
            assert data['current_exercise']['variants'] and 'history' not in data and 'trace' not in str(data)
            assert 'exercise' in schema['properties']
            assert schema['properties']['exercise']['enum']==['current','repeat']
            assert budget<=8 and 'RACECAST_CONSOLE_SECRET' not in env
            Path(invocation.output).write_text(json.dumps(dict(explanation=COACH_EXPLANATIONS[0],
                evidence=['coach.lap_time_s'],claims=[],exercise='current')),encoding='utf8')
            return b''
        result=driver_radio_ai.LiveAI(machine,adapter_factory=Adapter,process=process).explain('live',f.messages()[-1],threading.Event(),8)
        assert result['exercise']=='current' and len(seen)==1


def t_relay_attachment_has_one_shared_worker_and_disabled_sources_have_none():
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);live=SimpleNamespace(snapshot=lambda:copy.deepcopy(f.snap),detail=f.detail)
        attached=coach.attach(f.radio.service,live)
        assert attached is coach.attach(f.radio.service,live) and f.radio.service.coach is attached
        assert attached._thread is None, 'Handler construction started a worker before relay binding'
        attached.start();attached.start()
        thread=attached._thread;assert thread is not None and thread.is_alive()
        attached.close();assert not thread.is_alive()
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.radio.access.available=False
        assert coach.attach(f.radio.service,live) is None
        assert coach.attach(f.radio.service,None) is None
        f.radio.access.available=True
        for source in (SimpleNamespace(snapshot=lambda:{}),SimpleNamespace(detail=lambda _id:{})):
            try:attached=coach.attach(f.radio.service,source)
            except AttributeError:raise AssertionError('Incomplete live dependency crashed existing radio setup') from None
            assert attached is None


def t_ai_cannot_hide_a_new_driving_action_in_explanation_synonyms():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command(allow_ai=True);f.step()
        for n in (2,3):f.complete(n);f.step()
        message=f.messages()[-1]
        for prose in ('You could depress the accelerator completely.',
                      'You might select a higher ratio.',
                      'Consider a more aggressive trajectory.'):
            result=dict(explanation=prose,evidence=['coach.lap_time_s'],claims=[],exercise='repeat')
            try:providers.render_explanation(result,message)
            except providers.ProviderError as error:assert error.code=='invalid_explanation'
            else:raise AssertionError('Unbounded AI driving instruction passed: '+prose)


def t_revoking_the_active_owner_releases_the_training_slot_without_sharing_private_state():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command(owner=REMOTE);f.step()
        for n in (2,3):f.complete(n);f.step()
        f.granted=False;f.step()
        revoked=f.service.snapshot(REMOTE)
        assert not revoked['active'] and revoked['phase']=='forbidden'
        f.granted=True;state=f.command(owner=LOCAL)
        assert state['active'] and state['calibration'] is None and state['focus'] is None
        f.step();assert f.service.snapshot(LOCAL)['calibration']['calibration_ids']==[]


def t_live_detail_must_match_the_requested_summary_before_model_mutation():
    for field,value in (('lap',99),('time_s',99),('_start_wall',100),('_end_wall',200),('state','provisional')):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.command();f.step();row=f.complete(2)
            f.rows[row['id']][field]=value
            state=f.step()
            assert not state['calibration']['calibration_ids'] and not state['latest'],field


def t_only_stop_can_preempt_a_control_command_already_being_applied():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        entered=threading.Event();release=threading.Event();apply=f.service._apply
        def blocked(command):entered.set();assert release.wait(3);apply(command)
        f.service._apply=blocked;f.command('objective',objective='pace')
        thread=threading.Thread(target=f.service.step);thread.start();assert entered.wait(2)
        try:rejects(lambda:f.command('keep'),'busy')
        finally:release.set();thread.join(3)
        assert not thread.is_alive()


def t_an_obsolete_blocked_grant_check_cannot_discard_a_new_valid_command_model():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        before=f.service.snapshot(LOCAL)['focus']['focus']
        entered=threading.Event();release=threading.Event()
        def blocked():entered.set();assert release.wait(3);return False
        f.service._grant=blocked
        thread=threading.Thread(target=f.service.step);thread.start();assert entered.wait(2)
        f.command('keep');release.set();thread.join(3);assert not thread.is_alive()
        state=f.step()
        assert f.service._model is not None and state['active'] and state['focus']['focus']==before


def t_evicted_lap_ids_never_replay_old_reviews_but_old_baseline_corrections_still_invalidate():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in range(2,140):
            f.complete(n);state=f.step()
            assert state['latest']['lap_id']=='lap-'+str(n), (n,state['latest'])
        baseline=state['calibration']['baseline']['id'];before=state['latest']
        for _ in range(3):assert f.step()['latest']==before
        for brief in f.snap['laps']:
            if brief['id']=='lap-2':brief.update(measurement_hash='corrected-old-baseline',time_s=9)
        f.rows['lap-2'].update(measurement_hash='corrected-old-baseline',time_s=9)
        state=f.step()
        assert state['calibration']['baseline'] is None and state['latest'] is None
        assert state['calibration']['invalidations'][-1]['reason']=='source_evidence_corrected'
        assert any(e['baseline_id']==baseline for e in state['focus']['history'])
        f.complete(140);f.step();f.complete(141);f.step();f.complete(142);state=f.step()
        assert state['calibration']['baseline']['lap_ids']==['lap-141','lap-142']


def t_selected_reference_snapshot_io_cannot_block_status_or_stop_controls():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        entered=threading.Event();release=threading.Event();completed=threading.Event()
        cal=f.service._model['cal'];snapshot=cal.snapshot
        def blocked():entered.set();assert release.wait(3);return snapshot()
        cal.snapshot=blocked
        thread=threading.Thread(target=f.service.step);thread.start();assert entered.wait(2)
        control=threading.Thread(target=lambda:(f.command('stop'),completed.set()));control.start()
        try:assert completed.wait(1), 'Reference snapshot blocked the control state lock'
        finally:release.set();thread.join(3);control.join(3)
        assert not thread.is_alive() and not control.is_alive()


def t_a_normal_provisional_to_confirmed_lap_transition_is_not_a_correction_reset():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();row=f.complete(2)
        f.snap['laps'][-1]['state']='provisional';f.rows[row['id']]['state']='provisional'
        state=f.step();assert state['latest'] is None
        f.snap['laps'][-1]['state']='confirmed';f.rows[row['id']]['state']='confirmed'
        state=f.step()
        assert state['calibration']['calibration_ids']==[row['id']]
        assert not state['calibration']['invalidations']


def t_unavailable_and_pending_detail_cannot_starve_new_ready_laps():
    for missing in ('evicted','unavailable','pending'):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.command();f.step()
            for n in (2,3,4,5):f.complete(n);f.step()
            for n in range(6,20):f.complete(n)
            calls=[]
            def detail(ident,calls=calls,missing=missing,f=f):
                calls.append(ident)
                return {'state':missing} if int(ident.split('-')[1])<16 else f.detail(ident)
            f.service.detail=detail
            for _ in range(5):
                before=len(calls);state=f.step()
                assert len(calls)-before<=coach.ROWS_PER_STEP, 'Detail retry budget exceeded'
            assert state['latest']['lap_id']=='lap-19', (missing,state['latest'])
            assert state['detail_status']['state']==missing
            before=len(calls);f.step();assert len(calls)==before


def t_pending_detail_is_retried_without_a_newer_ready_lap():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();f.complete(2)
        f.service.detail=lambda _ident:{'state':'pending'}
        assert f.step()['latest'] is None
        f.service.detail=f.detail
        state=f.step();assert state['latest']['lap_id']=='lap-2'


def t_real_store_detail_eviction_recovers_after_a_delayed_worker():
    from test_gt7_laps import FakeCars,FakeTracks,write_circle_recording
    from race_engineer_live import Store
    import gt7_recording,gt7_telemetry
    with tempfile.TemporaryDirectory() as root:
        path=write_circle_recording(root,lap_secs=(20,)*32)
        packets=list(gt7_recording.Recording(path).packets());clock=[100.];wall=[packets[0][0]]
        with Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),queue_max=4096,clock=lambda:clock[0]) as live:
            radio=RadioFixture(root);radio.service.source=live.snapshot
            radio.service.clock=lambda:clock[0];radio.service.wall=lambda:wall[0]
            service=coach.Service('solo',live.snapshot,live.detail,radio.service,clock=lambda:clock[0],wall=lambda:wall[0])
            try:
                for i,(stamp,_kind,plain) in enumerate(packets):
                    wall[0]=stamp;clock[0]=100+stamp-packets[0][0]
                    live.put(gt7_telemetry.parse_packet(plain),stamp)
                    if i==9:
                        assert live.flush(10);service.command(LOCAL,'start',0,{},authorize=lambda:True);service.step()
                    elif i%400==399:
                        assert live.flush(10)
                        if (live.snapshot().get('current') or {}).get('lap',999)<=6:service.step()
                assert live.flush(10)
                doc=live.snapshot();assert service._model['stamp']==coach._stamp(doc)
                assert any(r['lap']==6 and live.detail(r['id'])['state']=='evicted' for r in doc['laps'])
                newest=max((r for r in doc['laps'] if r['state']=='confirmed'),key=lambda r:r['lap'])
                assert live.detail(newest['id'])['state']=='ready'
                for _ in range(10):service.step()
                state=service.snapshot(LOCAL)
                assert state['latest']['lap_id']==newest['id'], 'Evicted details blocked newer ready evidence'
                assert state['detail_status']['state']=='evicted'
            finally:service.close();radio.service.close()


def t_training_trust_boundaries_reject_foreign_owners_and_truthy_grants():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root)
        for owner in ({'profile':'other','subject':'local:producer'},
                      {'profile':'solo','subject':'untrusted'},
                      dict(LOCAL,extra=True)):
            rejects(lambda owner=owner:f.service.command(owner,'start',0,{},authorize=lambda:True),'invalid_owner')
        for grant in (lambda:1,lambda:'yes',None):
            rejects(lambda grant=grant:f.service.command(LOCAL,'start',0,{},authorize=grant),'forbidden')
        f.command()
        rejects(lambda:f.service.command(LOCAL,'stop',True,{},authorize=lambda:True),'conflict')
        f.step()
        f.service.close()
        rejects(f.command,'service_closed')


def t_wall_watermark_excludes_later_numbered_laps_started_before_consent():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        f.complete(2,_start_wall=999)
        state=f.step();assert not state['latest'] and not state['calibration']['calibration_ids']


def t_frozen_detail_identity_cannot_be_substituted_and_source_session_cannot_mix():
    for field,value in (('id','foreign'),('session_id','other'),('measurement_hash','other'),
                        ('version',99),('context_revision',99)):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root);f.command();f.step();row=f.complete(2);f.rows[row['id']][field]=value
            state=f.step();assert not state['latest'] and not state['calibration']['calibration_ids'],field
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();f.complete(2,session_id='foreign')
        state=f.step();assert not state['latest'] and not state['calibration']['calibration_ids']


def t_radio_grant_io_cannot_block_stop_or_revalidate_an_obsolete_training_token():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step();f.complete(2);f.step();f.at+=2
        ticket=f.radio.service.take_playback();assert ticket is not None
        entered=threading.Event();release=threading.Event();done=threading.Event()
        def grant():entered.set();assert release.wait(3);return True
        f.service._grant=grant
        messages=[]
        reader=threading.Thread(target=lambda:messages.extend(f.messages()));reader.start();assert entered.wait(1)
        control=threading.Thread(target=lambda:(f.command('stop'),done.set()));control.start()
        try:
            assert done.wait(.5), 'Radio grant I/O blocked Stop'
            assert ticket.cancelled.is_set(), 'Stop did not immediately cancel the active coach ticket'
        finally:release.set();reader.join(4);control.join(4)
        assert not reader.is_alive() and not control.is_alive()
        assert messages[-1]['state']=='cancelled', 'Delayed grant revalidated an obsolete training token'


def t_publication_rechecks_the_training_token_after_a_delayed_grant():
    with tempfile.TemporaryDirectory() as root:
        f=Fixture(root);f.command();f.step()
        token=f.service.snapshot(LOCAL)['epoch']
        entered=threading.Event();release=threading.Event();results=[]
        def grant():entered.set();assert release.wait(3);return True
        f.service._grant=grant
        reader=threading.Thread(target=lambda:results.append(f.service._current(token)))
        reader.start();assert entered.wait(1)
        try:
            assert f.command()['active']
            state=f.step();assert state['active'] and not state['pending']
        finally:release.set();reader.join(4)
        assert not reader.is_alive() and results==[False], 'Delayed grant authorized an obsolete publication'


if __name__=='__main__':
    tests=[(n,f) for n,f in globals().copy().items() if n.startswith('t_') and callable(f)]
    for n,f in tests:f();print('PASS',n)
    print('PASS',len(tests),'live coach scenarios')

#!/usr/bin/env python3
"""Live question preparation uses fake agents, speech and native devices only."""
import copy
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_providers as providers
from test_driver_radio_service import Fixture,features
from test_driver_radio_providers import wav

QUESTION='What do these measurements suggest about my situation?'


def configured(f,speech=True,agent='fixture'):
    f.access.update({'expected':f.access.read()['revision'],
        'features':features(True,True,agent),'providers':{'speech':speech,'engine':'google'}})
    return f.poll()


def answer(message):
    fact=providers.compact_facts(message)['fuel.usable_laps']
    return dict(explanation='The measured reserve may leave room.',evidence=['fuel.usable_laps'],
        claims=[dict(fact='fuel.usable_laps',**fact)])


class AI:
    def __init__(self):self.calls=[]
    def explain(self,agent,message,stopped,budget):
        self.calls.append((agent,copy.deepcopy(message),budget))
        return answer(message)


class Speech:
    def __init__(self):self.calls=[]
    def synthesize(self,text,stopped,budget):
        self.calls.append((text,budget));return wav()


def current(f,message):
    return next(m for m in f.service.history()['messages'] if m['id']==message['id'])


def t_unknown_question_uses_existing_agent_then_synthesizes_its_grounded_answer():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);ai=AI();speech=Speech()
        worker=providers.Enricher(f.service,ai=ai,speech=speech,clock=lambda f=f:f.at)
        message=f.service.question(QUESTION,state['binding'])
        assert message.get('answer_mode')=='live_ai' and message['ai_state']=='pending',message
        assert message['text']=='Checking the current telemetry for that question.'
        assert worker.step('audio') is False and speech.calls==[],'No fallback speech before the live answer'
        assert worker.step('ai') is True
        ready=current(f,message)
        assert ready['ai_state']=='ready' and ready['text'].startswith('AI interpretation. The measured reserve')
        assert 'Fuel range after reserve:' in ready['text'] and 'explanation' not in ready
        assert ai.calls[0][0]=='fixture' and ai.calls[0][1]['question']==QUESTION and 0<ai.calls[0][2]<=8
        assert worker.step('audio') is True and speech.calls[0][0]==ready['text'] and 0<speech.calls[0][1]<=8
        assert f.service.hud_text('engineer') is None,'AI prose must not become deterministic HUD text'
        next_question=f.service.question('What does that imply?',state['binding'])
        assert next_question['conversation'][-1]['answer']==ready['text']


def t_known_questions_and_control_refusals_keep_the_fast_existing_path():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);ai=AI();speech=Speech()
        worker=providers.Enricher(f.service,ai=ai,speech=speech,clock=lambda f=f:f.at)
        known=f.service.question('Fuel range?',state['binding'])
        assert 'answer_mode' not in known
        assert worker.step('audio') is True and speech.calls[-1][0]==known['text'] and ai.calls==[]
        refusal=f.service.question('delete OBS',state['binding'])
        assert refusal['ai_state']=='not_requested' and 'control commands are unavailable' in refusal['text']
        assert 'answer_mode' not in refusal
        f=Fixture(Path(d)/'no-agent');state=configured(f,agent=None)
        unknown=f.service.question(QUESTION,state['binding'])
        assert unknown['ai_state']=='not_requested' and unknown['text'].startswith("I can't answer that yet.")


def t_live_answer_deadline_releases_fallback_and_rejects_a_late_completion():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);speech=Speech();entered=threading.Event();release=threading.Event();outcomes=[]
        class Blocked(AI):
            def explain(self,*args):
                entered.set();assert release.wait(3);return super().explain(*args)
        worker=providers.Enricher(f.service,ai=Blocked(),speech=speech,clock=lambda f=f:f.at)
        message=f.service.question(QUESTION,state['binding'])
        thread=threading.Thread(target=lambda worker=worker,outcomes=outcomes:outcomes.append(worker.step('ai')));thread.start()
        assert entered.wait(1)
        try:
            f.at+=8;f.poll();expired=current(f,message)
            assert expired['ai_state']=='failed' and expired['ai_error']=='timed_out',expired
            assert expired['text'].startswith("I can't answer that yet.")
            assert worker.step('audio') is True and speech.calls[0][0]==expired['text']
        finally:release.set();thread.join(3)
        assert not thread.is_alive() and outcomes==[False]
        assert current(f,message)['text']==expired['text'] and len(speech.calls)==1


def t_failed_or_invalid_live_answers_preserve_fixed_fallback():
    for error in ('timed_out','busy','invalid_explanation'):
        with tempfile.TemporaryDirectory() as d:
            f=Fixture(d);state=configured(f);speech=Speech()
            class Failed:
                def explain(self,*args,error=error):raise providers.ProviderError(error)
            worker=providers.Enricher(f.service,ai=Failed(),speech=speech,clock=lambda f=f:f.at)
            message=f.service.question(QUESTION,state['binding'])
            assert worker.step('ai') is True
            result=current(f,message)
            assert result['ai_state']=='failed' and result['ai_error']==error and result['text'].startswith("I can't answer that yet."),result
            assert worker.step('audio') is True and speech.calls[0][0]==result['text']
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);ai=AI();worker=providers.Enricher(f.service,ai=ai,clock=lambda f=f:f.at)
        f.service.question(QUESTION,state['binding']);assert worker.step('ai')
        second=f.service.question('What can you infer from this?',state['binding']);assert worker.step('ai')
        assert len(ai.calls)==1 and current(f,second)['ai_error']=='rate_limited'


def t_non_live_or_revoked_work_cannot_publish_or_speak_an_agent_answer():
    for mutation in ('replay','session','feature','agent'):
        with tempfile.TemporaryDirectory() as d:
            f=Fixture(d);state=configured(f);speech=Speech();entered=threading.Event();release=threading.Event();outcomes=[]
            class Blocked(AI):
                def explain(self,*args,entered=entered,release=release):entered.set();assert release.wait(3);return super().explain(*args)
            worker=providers.Enricher(f.service,ai=Blocked(),speech=speech,clock=lambda f=f:f.at)
            f.service.question(QUESTION,state['binding'])
            thread=threading.Thread(target=lambda worker=worker,outcomes=outcomes:outcomes.append(worker.step('ai')));thread.start();assert entered.wait(1)
            try:
                if mutation=='replay':f.snap['replay']={'active':True};f.snap['revision']+=1
                elif mutation=='session':f.snap['session_id']='replacement';f.snap['revision']+=1
                elif mutation=='feature':f.access.update({'expected':f.access.read()['revision'],'features':features(False,True,'fixture')})
                else:f.access.update({'expected':f.access.read()['revision'],'features':features(True,True,None)})
                f.poll()
            finally:release.set();thread.join(3)
            assert not thread.is_alive() and outcomes==[False],mutation
            assert not any(m['text'].startswith('AI interpretation.') for m in f.service.history()['messages']),mutation
            assert worker.step('audio') is False and speech.calls==[],mutation


def t_text_only_live_answer_does_not_requeue_speech():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f,speech=False);ai=AI();speech=Speech()
        worker=providers.Enricher(f.service,ai=ai,speech=speech,clock=lambda f=f:f.at)
        message=f.service.question(QUESTION,state['binding'])
        assert worker.step('audio') is True and speech.calls==[]
        ticket=f.service.take_playback();assert ticket and f.service.finish_playback(ticket.id,'text_only')
        assert worker.step('ai') is True
        result=current(f,message)
        assert result['state']=='text_only' and result['text'].startswith('AI interpretation.')
        assert worker.step('audio') is False and f.service.take_playback() is None and speech.calls==[]


def t_native_live_answer_is_spoken_once_and_capture_waits_for_it():
    from test_driver_radio_native import Harness
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);configured(h.f);ai=AI();speech=Speech()
        worker=providers.Enricher(h.f.service,ai=ai,speech=speech,clock=lambda:h.f.at)
        try:
            h.n.step();h.workers[-1].events=[{'event':'question','text':QUESTION}]
            h.n.step();assert h.workers[-1].closed and h.f.service.pending_count()==1
            message=h.f.service.enrichment_work('ai');assert message['answer_mode']=='live_ai'
            h.n.step();assert h.f.service._ticket and not h.players
            assert not h.f.service.attach(message['id'],message['epoch'],{'audio':wav(),'audio_state':'ready'})
            assert worker.step('audio') is False and worker.step('ai') is True and worker.step('audio') is True
            h.n.step();assert len(h.players)==1 and len(speech.calls)==1
            assert current(h.f,message)['text']==speech.calls[0][0]
            h.players[0].done=True;h.n.step();h.n.step();h.n.step()
            assert len(h.players)==1 and current(h.f,message)['state']=='spoken'
            assert worker.step('audio') is False and len(ai.calls)==1
        finally:h.n.close();worker.close()


def t_invalid_agent_numbers_cannot_become_live_answer_or_speech():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);speech=Speech()
        class Invented(AI):
            def explain(self,*args):
                result=super().explain(*args);result['claims'][0]['value']=999;return result
        worker=providers.Enricher(f.service,ai=Invented(),speech=speech,clock=lambda:f.at)
        message=f.service.question(QUESTION,state['binding'])
        assert worker.step('ai') is True
        invalid=current(f,message)
        assert invalid['ai_state']=='failed' and invalid.get('ai_error')=='invalid_explanation' and '999' not in invalid['text']
        assert worker.step('audio') and speech.calls[0][0]==invalid['text']


def t_urgent_fuel_preempts_a_pending_live_answer_without_waiting_for_the_agent():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);entered=threading.Event();release=threading.Event();outcomes=[];speech=Speech()
        class Blocked(AI):
            def explain(self,*args):entered.set();assert release.wait(3);return super().explain(*args)
        worker=providers.Enricher(f.service,ai=Blocked(),speech=speech,clock=lambda:f.at)
        f.service.question(QUESTION,state['binding']);ticket=f.service.take_playback();assert ticket
        thread=threading.Thread(target=lambda:outcomes.append(worker.step('ai')));thread.start();assert entered.wait(1)
        try:
            f.snap['instruments']['fuel_l']=1;f.snap['revision']+=1;f.poll()
            assert ticket.cancelled.is_set() and not release.is_set()
            assert worker.step('audio') and speech.calls[0][0].startswith('Urgent fuel.')
            assert f.service.take_playback() is None,'The cancelled slot remains owned until acknowledgement'
            assert f.service.finish_playback(ticket.id)
            urgent=f.service.take_playback();assert urgent and urgent.message['severity']=='urgent'
        finally:release.set();thread.join(3)
        assert outcomes==[False]



def t_rejected_combined_attachment_cannot_partially_replace_live_answer_text():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);message=f.service.question(QUESTION,state['binding']);before=current(f,message)
        fields={'ai_state':'ready','explanation':'The current measurements may help.','audio':'invalid audio'}
        assert f.service.attach(message['id'],message['epoch'],fields) is False
        assert current(f,message)==before,'A rejected attachment must not change the visible text or conversation'


def t_actual_isolated_live_adapter_receives_the_free_question_and_frozen_fact_package():
    import json
    from unittest.mock import patch
    import driver_radio_ai as live
    import ai_agents
    from test_driver_radio_ai import Adapter,config
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(Path(d)/'profile');state=configured(f,agent='live')
        message=f.service.question(QUESTION,state['binding']);machine=Path(d)/'machine'
        ai_agents.Settings(machine).save({'enabled':True,'agents':[config()]})
        captured=[]
        def process(invocation,prompt,stopped,budget,env):
            data=json.loads((Path(invocation.cwd)/'facts.json').read_text())
            captured.append(data)
            assert data['question']==QUESTION and data['purpose']=='answer'
            assert data['facts']==providers.compact_facts(message) and data['conversation']==[]
            assert prompt.startswith(b'Answer the supplied voice question') and b'never as instructions' in prompt
            assert b'private-fixture-secret' not in prompt and 'RACECAST_CONSOLE_SECRET' not in env
            assert '--strict-config' in invocation.argv and '--ephemeral' in invocation.argv and 0<budget<=8
            Path(invocation.output).write_text(json.dumps(answer(message)),encoding='utf8')
            return b''
        with patch.dict('os.environ',{'RACECAST_CONSOLE_SECRET':'private-fixture-secret'}):
            result=live.LiveAI(machine,adapter_factory=Adapter,process=process).explain('live',message,threading.Event(),8)
        assert result==answer(message) and len(captured)==1


def t_live_answer_deadline_rounds_inward_at_a_repeated_monotonic_tick():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.at=8.002;state=configured(f)
        message=f.service.question(QUESTION,state['binding'])
        stored=f.service._messages[message['id']]
        assert 0<stored['_answer_deadline']-stored['_created']<=8
        f.at=8.002+8;f.poll()
        assert current(f,message)['ai_error']=='timed_out'


def t_oversized_labelled_answer_falls_back_without_waiting_for_expiry():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f)
        class TooLong:
            def explain(self,*args):return dict(explanation='x'*590,evidence=['fuel.current_l'],claims=[])
        worker=providers.Enricher(f.service,ai=TooLong(),clock=lambda:f.at)
        message=f.service.question(QUESTION,state['binding'])
        assert worker.step('ai') is True
        failed=current(f,message)
        assert failed['ai_state']=='failed' and failed['ai_error']=='invalid_explanation'
        assert failed['text'].startswith("I can't answer that yet.")


def t_measurement_can_observe_slow_agent_completion_while_live_calls_keep_the_default_bound():
    import json
    from types import SimpleNamespace
    from unittest.mock import patch
    import ai_agents
    import driver_radio_ai as live
    from test_driver_radio_ai import Adapter,config
    with tempfile.TemporaryDirectory() as d:
        machine=Path(d)/'machine';ai_agents.Settings(machine).save({'enabled':True,'agents':[config()]})
        f=Fixture(Path(d)/'profile');state=configured(f,agent='live');message=f.service.question(QUESTION,state['binding'])
        at=[100.0];budgets=[]
        def process(invocation,prompt,stopped,budget,env):
            budgets.append(budget);at[0]+=12
            Path(invocation.output).write_text(json.dumps(answer(message)),encoding='utf8');return b''
        with patch.object(live,'time',SimpleNamespace(monotonic=lambda:at[0])):
            measured=live.LiveAI(machine,adapter_factory=Adapter,process=process,max_budget=30)
            assert measured.explain('live',message,threading.Event(),30)==answer(message) and budgets[-1]==30
            at[0]=100;bounded=live.LiveAI(machine,adapter_factory=Adapter,process=process)
            try:bounded.explain('live',message,threading.Event(),30)
            except providers.ProviderError as exc:assert exc.code=='timed_out' and budgets[-1]==8
            else:raise AssertionError('Production LiveAI accepted a reply outside its existing budget')
        for value in (True,7,61,float('nan'),float('inf')):
            try:live.LiveAI(machine,max_budget=value)
            except ValueError:pass
            else:raise AssertionError('Unbounded measurement budget accepted')


def t_free_question_requires_live_data_even_with_an_agent_selected():
    for condition in ('stale','replay'):
        with tempfile.TemporaryDirectory() as d:
            f=Fixture(d);configured(f)
            if condition=='stale':f.snap['receipt']={'state':'stale'}
            else:f.snap['replay']={'active':True}
            f.snap['revision']+=1;state=f.poll()
            message=f.service.question(QUESTION,state['binding'])
            assert message['state']=='text_only' and message['ai_state']=='not_requested' and 'answer_mode' not in message
            assert f.service.enrichment_work('ai') is None and f.service.enrichment_work('audio') is None


def t_ai_cancel_deadline_does_not_depend_on_a_history_reader_or_cancel_fallback_speech():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);message=f.service.question(QUESTION,state['binding'])
        assert f.service.provider_current(message['id'],message['epoch'],kind='ai')
        f.at+=8  # No refresh/history call has settled the cached message yet.
        assert not f.service.provider_current(message['id'],message['epoch'],kind='ai')
        assert f.service.provider_current(message['id'],message['epoch'],kind='audio')


def t_settled_failure_cannot_be_replaced_by_an_early_but_obsolete_agent_reply():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);message=f.service.question(QUESTION,state['binding'])
        assert f.service.attach(message['id'],message['epoch'],{'ai_state':'failed','ai_error':'rate_limited'})
        before=current(f,message)
        assert not f.service.attach(message['id'],message['epoch'],{'ai_state':'ready','explanation':'The measurements may help.'})
        assert current(f,message)==before


def t_answer_deadline_is_checked_again_after_refresh_before_mutating_text():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);message=f.service.question(QUESTION,state['binding'])
        refresh=f.service.refresh
        def crossed(*args,**kwargs):
            result=refresh(*args,**kwargs);f.at+=8;return result
        with patch.object(f.service,'refresh',side_effect=crossed):
            assert not f.service.attach(message['id'],message['epoch'],{'ai_state':'ready','explanation':'The measurements may help.'})
        assert current(f,message)['ai_error']=='timed_out'


def t_invalid_direct_answer_attachment_leaves_pending_text_unchanged():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=configured(f);message=f.service.question(QUESTION,state['binding']);before=current(f,message)
        for fields in ({'ai_state':'ready'},{'ai_state':'ready','explanation':''},
                       {'ai_state':'ready','explanation':'x'*590}):
            assert not f.service.attach(message['id'],message['epoch'],fields)
            assert current(f,message)==before


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

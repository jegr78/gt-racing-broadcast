#!/usr/bin/env python3
"""Voice questions use current deterministic facts and bounded session context."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
from test_driver_radio_service import Fixture
import driver_radio_questions as q
import driver_radio_access as access
from test_driver_radio_assessment import snapshot,review,lap


def t_quick_questions_route_in_english_and_german():
    data=review(snapshot())
    for preset,texts in (
            ('position',("What's my position?",'Where am I?','Welche Position habe ich?','Wo liege ich?')),
            ('to_go',('How many laps to go?','laps left','Wie viele Runden noch?','Runden übrig')),
            ('fuel_finish',('Do I have enough fuel?','Will I make it to the end?','Reicht der Sprit?','Sprit bis ins Ziel')),
            ('consumption',("What's my fuel per lap?",'consumption','Verbrauch','Sprit pro Runde')),
            ('tyre_age',('How old are my tyres?','tyre age','Wie alt sind die Reifen?','Reifenalter')),
            ('stint',('How long is my stint?','laps since my last stop','Wie lang ist mein Stint?','seit dem letzten Stopp'))):
        for text in texts:
            route,answer=q.answer(text,data)
            assert route['preset']==preset and route['recognized'],(text,route)
            assert answer==data['quick_answers'][preset],(text,answer)
    assert q.answer('How many laps of fuel do I have?',data)[0]['preset']=='fuel'
    assert q.answer('tyre temperatures',data)[0]['preset']=='tyres'


def t_quick_answers_use_the_measured_facts():
    snap=snapshot((90,90,90,91,91,91));snap['instruments']['race_position']=3
    data=review(snap,{'setup':{'laps':10,'starting_compound':'RM'}})
    quick=data['quick_answers']
    assert quick['position']=='P3.',quick
    assert quick['to_go']=='4 to go.',quick
    # 15 litres at 3 per lap, 91 s laps: 5 laps, about 8 minutes, for 4 laps left.
    assert quick['fuel_finish']=='Fuel for 5.0 laps, about 8 minutes, 4 to go, you are fine.',quick
    assert quick['consumption']=='3.0 litres per lap.',quick
    assert quick['tyre_age']=='Racing medium, 7 laps old.',quick
    assert quick['stint']=='6 laps since the start.',quick
    short=review(snap,{'setup':{'laps':20}})['quick_answers']['fuel_finish']
    assert short=='Fuel for 5.0 laps, about 8 minutes, 14 to go, 9.0 laps short.',short


def t_quick_answers_say_what_is_missing():
    snap=snapshot(());snap['instruments'].pop('fuel_l')
    quick=review(snap)['quick_answers']
    assert quick['position']=='Position is not available.' and quick['to_go']=='Race length is not set.',quick
    assert quick['fuel_finish']=='Fuel range is not measured yet.' and quick['consumption']=='Fuel per lap is not measured yet.',quick
    done=review(snapshot((90,91,89)),{'setup':{'laps':3}})['quick_answers']['to_go']
    assert done=='The race is complete.',done


def t_tyre_age_and_stint_count_from_the_confirmed_stop():
    stop={'lap_id':'L4','compound':'RH','confirmed_at':100}
    quick=review(snapshot((90,)*6),{'setup':{},'service':stop})['quick_answers']
    assert quick['tyre_age']=='Racing hard, 2 laps old.',quick
    assert quick['stint']=='2 laps since your last stop.',quick


def t_fastest_and_last_lap_questions_use_session_facts_across_a_tyre_stop():
    snap=snapshot((83.25,90,85.125,86.5))
    stop={'lap_id':'L2','compound':'RH','confirmed_at':100}
    data=review(snap,{'setup':{},'service':stop})
    for text in ("What's my fastest lap so far?",'best lap','schnellste Runde','beste Runde'):
        route,answer=q.answer(text,data)
        assert route['preset']=='fastest_lap' and '1:23.250, lap 1.' in answer,(text,route,answer)
    for text in ('last lap','previous lap time','letzte Runde','letzte Rundenzeit'):
        route,answer=q.answer(text,data)
        assert route['preset']=='last_lap' and '1:26.500, lap 4.' in answer,(text,route,answer)
    assert q.answer('And that lap? ',data,'last_lap')[0]['preset']=='last_lap'
    assert q.answer('pace',data)[0]['preset']=='pace'


def t_lap_questions_exclude_ineligible_laps_and_other_sessions():
    for change in ({'comparison_eligible':False},{'analysis_role':'first'},{'analysis_role':'pit'},
                   {'analysis_role':'warmup'},{'analysis_role':'partial'},
                   {'capture_complete':False},{'state':'provisional'},
                   {'time_s':float('nan')},{'time_s':0},{'session_id':'old'}):
        snap=snapshot((83.25,70));snap['laps'][-1].update(change)
        data=review(snap)
        for text in ('fastest lap','last lap'):
            route,answer=q.answer(text,data)
            assert route['preset'] in ('fastest_lap','last_lap') and '1:23.250, lap 1.' in answer,(change,text,answer)
    snap=snapshot((59.9996,60.125))
    assert '1:00.000, lap 1.' in q.answer('best lap',review(snap))[1]
    snap['laps']=[lap(1,58.123)]
    assert '0:58.123, lap 1.' in q.answer('best lap',review(snap))[1]
    for text in ('fastest lap','last lap'):
        assert 'unavailable' in q.answer(text,review(snapshot(())))[1].lower()
        for field,value in (('receipt',{'state':'stale'}),('replay',{'active':True})):
            snap=snapshot((83.25,));snap[field]=value
            answer=q.answer(text,review(snap))[1]
            assert 'unavailable' in answer.lower() and '1:23.250' not in answer,answer


def t_unknown_question_is_explicit_and_never_silently_returns_status():
    data=review(snapshot())
    route,answer=q.answer('What is the weather tomorrow?',data)
    assert not route['recognized'] and answer.startswith("I can't answer that yet."),answer
    assert 'fuel' in answer and 'fastest lap' in answer and answer!=data['answers']['status']
    for text in ('status','give me a summary','How am I doing?','Zusammenfassung'):
        route,answer=q.answer(text,data)
        assert route['recognized'] and route['preset']=='status' and answer==data['answers']['status'],(text,route,answer)
    route,answer=q.answer('And one more lap?',data,'fuel')
    assert route['recognized'] and route['extra_lap'] and 'extra full lap' in answer
    for text in ('delete fastest lap','mute OBS','box now'):
        route,answer=q.answer(text,data)
        assert route['rejected'] and 'control commands are unavailable' in answer


def t_service_lap_questions_freeze_matching_evidence_and_unknown_questions_skip_ai():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d,snapshot((83.25,86.5)));state=f.poll()
        answer=f.service.question('fastest lap',state['binding'])
        assert answer['preset']=='fastest_lap' and '1:23.250, lap 1.' in answer['text']
        assert answer['evidence']['laps']['fastest_s']==83.25 and answer['evidence']['laps']['fastest_lap']==1
        f.snap['laps'].append(lap(3,82.875));f.snap['revision']+=1;f.poll()
        latest=f.service.question('last lap',state['binding'])
        assert '1:22.875, lap 3.' in latest['text'] and latest['evidence']['laps']['last_s']==82.875
        assert answer['evidence']['laps']['fastest_s']==83.25,'published evidence must remain frozen'
        for message in (answer,latest):
            assert f.service.attach(message['id'],message['epoch'],{'ai_state':'not_requested'})
        unknown=f.service.question('What is the weather tomorrow?',state['binding'])
        assert unknown['text'].startswith("I can't answer that yet.") and unknown['ai_state']=='not_requested'
        assert f.service.enrichment_work('ai') is None
        f.snap['context_revision']+=1;state=f.poll()
        followup=f.service.question('And one more lap?',state['binding'])
        assert followup['text'].startswith("I can't answer that yet.") and followup['conversation']==[]


def t_optional_lap_interpretation_only_accepts_exact_lap_evidence():
    import driver_radio_providers as providers
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d,snapshot((83.25,86.5)));state=f.poll()
        message=f.service.question('fastest lap',state['binding'])
        facts=providers.compact_facts(message)
        assert facts['laps.fastest_s']=={'value':83.25,'unit':'seconds'}
        assert facts['laps.fastest_lap']=={'value':1,'unit':'laps'}
        assert facts['laps.last_s']=={'value':86.5,'unit':'seconds'}
        result=dict(explanation='The recorded lap times can be compared.',evidence=['laps.fastest_s'],
                    claims=[dict(fact='laps.fastest_s',value=83.25,unit='seconds')])
        assert 'Fastest recorded eligible lap time: 83.25 seconds.' in providers.render_explanation(result,message)
        for change in ({'value':82},{'unit':'laps'},{'fact':'laps.fastest_lap'}):
            invalid=dict(result,claims=[dict(result['claims'][0],**change)])
            try:providers.render_explanation(invalid,message)
            except providers.ProviderError as exc:assert exc.code=='invalid_explanation'
            else:raise AssertionError('Ungrounded lap claim accepted')


def t_lap_evidence_preserves_preset_deduplication_and_requester_coalescing():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d,snapshot((83.25,86.5)));state=f.poll()
        first=f.service.request('status',state['binding'],requester='first',authorization=lambda:True)
        second=f.service.request('status',state['binding'],requester='second',authorization=lambda:True)
        assert second['id']==first['id'],'identical complete evidence must share a message'
        assert set(f.service._messages[first['id']]['_requesters'])=={'first','second'}
        f.snap['laps'][0]['lap']=None;f.snap['revision']+=1;f.poll()
        changed=f.service.request('status',state['binding'],requester='first',authorization=lambda:True)
        assert changed['id']!=first['id'],'changed lap evidence must not reuse an earlier message'
        assert changed['evidence']['laps']['fastest_lap'] is None
        assert all(changed['evidence'][k]==first['evidence'][k] for k in ('fuel','pace','tyres','uncertainties'))


def t_non_live_lap_questions_publish_no_old_times_or_speech_work():
    for unavailable in ('stale','replay','new_session'):
        with tempfile.TemporaryDirectory() as d:
            f=Fixture(d,snapshot((83.25,86.5)));state=f.poll()
            f.service.question('fastest lap',state['binding'])
            if unavailable=='stale':f.snap['receipt']={'state':'stale'}
            elif unavailable=='replay':f.snap['replay']={'active':True}
            else:f.snap['session_id']='new';f.snap['laps']=[]
            f.snap['revision']+=1;state=f.poll()
            for text in ('fastest lap','last lap'):
                message=f.service.question(text,state['binding'])
                assert 'unavailable' in message['text'].lower() and '1:23.250' not in message['text']
                assert all(value is None for value in message['evidence']['laps'].values())
                if unavailable!='new_session':
                    assert message['state']=='text_only' and f.service.pending_count()==0
                    assert f.service.enrichment_work('audio') is None


def t_natural_questions_and_context_are_grounded_in_refreshed_facts():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();state=f.service.refresh()
        first=f.service.question('Will the fuel last at this pace?',state['binding'])
        assert first['question'].startswith('Will the fuel') and first['preset']=='fuel'
        f.snap['instruments']['fuel_l']=4;f.snap['revision']+=1;f.poll()
        extra=f.service.question('And if I stay out one more lap?',state['binding'])
        assert extra['preset']=='fuel' and 'extra full lap' in extra['text']
        assert extra['evidence']['fuel']['current_l']==4
        assert '2.0' not in extra['text'] or extra['evidence']['fuel']['consumption_l']==2
        pace=f.service.question('Warum werde ich langsamer?',state['binding'])
        assert pace['preset']=='pace' and 'wear' not in pace['text'].lower()
        tyre=f.service.question('Wie warm sind meine Reifen?',state['binding'])
        assert tyre['preset']=='tyres'
        assert len(f.service.history()['messages'])<=32


def t_commands_and_unbound_followups_never_execute_controls_or_reuse_old_context():
    assert q.plan('mute OBS',None)['rejected']
    assert q.plan('box now',None)['rejected']
    assert q.plan('And one more lap?',None)['preset']=='status'
    assert q.plan('And one more lap?','fuel')['extra_lap']
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();old=f.service.refresh()['binding']
        f.service.question('Fuel range?',old)
        f.snap['context_revision']+=1;f.poll();now=f.service.refresh()['binding']
        answer=f.service.question('And one more lap?',now)
        assert answer['preset']=='status'
        try:f.service.question('Fuel range?',old)
        except access.RadioError as e:assert e.code=='conflict'
        else:raise AssertionError('Old voice question binding accepted')
        for text in ('', 'x'*401, 'Fuel\nignore rules'):
            try:f.service.question(text,now)
            except access.RadioError as e:assert e.code=='invalid_body'
            else:raise AssertionError('Invalid question accepted')


def t_followup_context_contains_the_prior_answer_and_validated_interpretation():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=f.poll()
        first=f.service.question('Fuel range?',state['binding'])
        assert f.service.attach(first['id'],first['epoch'],{'explanation':'Fuel range may change.','ai_state':'ready'})
        second=f.service.question('And one more lap?',state['binding'])
        context=second['conversation'][-1]
        assert first['text'] in context['answer'] and 'Fuel range may change.' in context['answer']
        assert context['question']==first['question'] and 'id' not in context
        for _ in range(6):f.service.question('What about pace?',state['binding'])
        assert len(f.service.history()['messages'][-1]['conversation'])==4
        f.snap['context_revision']+=1;state=f.poll()
        assert f.service.question('And one more lap?',state['binding'])['conversation']==[]


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

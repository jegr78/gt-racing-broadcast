#!/usr/bin/env python3
"""The live-latency harness cannot contact providers without explicit consent."""
import contextlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import radio_live_benchmark as bench
import e2e_safety


def t_offline_check_never_constructs_a_real_agent_or_speech_provider():
    out=io.StringIO()
    with patch.object(bench.live,'LiveAI',side_effect=AssertionError('Real agent constructed')),\
            patch.object(bench.providers,'GoogleSpeech',side_effect=AssertionError('Real speech constructed')),\
            contextlib.redirect_stdout(out):
        assert bench.main(['--check'])==0
    result=json.loads(out.getvalue())
    assert result['check'] and result['provider_calls']==0 and result['ok'] and not result['playback']
    assert result['facts_source']=='synthetic' and result['timing_origin']=='synthetic_check'
    assert result['simulated_calls']=={'agent':2,'speech':2}


def t_provider_consent_and_parameters_are_checked_before_constructing_clients():
    cases=[[],['--allow-agent'],['--allow-agent','--agent','fixture','--machine-runtime','fixture'],
           ['--allow-agent','--agent','fixture','--machine-runtime','fixture','--speech','google'],
           ['--allow-agent','--agent','fixture','--machine-runtime','fixture','--speech','kokoro','--question','delete OBS']]
    with patch.object(bench.live,'LiveAI',side_effect=AssertionError('Real agent constructed')),\
            patch.object(bench.providers,'GoogleSpeech',side_effect=AssertionError('Real speech constructed')),\
            patch.object(e2e_safety,'enter',side_effect=AssertionError('Provider admission reached before consent')),\
            contextlib.redirect_stderr(io.StringIO()):
        for argv in cases:
            try:bench.main(argv)
            except SystemExit as exc:assert exc.code==2
            else:raise AssertionError('Unconfirmed provider work accepted')


def t_timings_keep_agent_and_synthesis_costs_separate_and_failed_ai_skips_speech():
    at=[0.0];calls=[]
    class AI:
        def explain(self,*args):
            at[0]+=2
            return dict(explanation='The measurements may help.',evidence=['fuel.current_l'],
                claims=[dict(fact='fuel.current_l',value=15,unit='litres')])
    class Speech:
        def synthesize(self,text,*args):
            from test_driver_radio_providers import wav
            calls.append(text);at[0]+=1;return wav()
    result=bench.measure(AI(),Speech(),'fixture',bench.sample(),attempts=1,clock=lambda:at[0])
    row=result['attempts'][0]
    assert row['ai_s']==2 and row['speech_s']==1 and row['time_to_audio_s']==3 and row['audio_seconds']==.1
    assert calls[0].startswith(bench.providers.ANSWER_PREFIX)
    class Timeout:
        def explain(self,*args):at[0]+=8;raise bench.providers.ProviderError('timed_out')
    calls.clear();result=bench.measure(Timeout(),Speech(),'fixture',bench.sample(),attempts=1,clock=lambda:at[0])
    row=result['attempts'][0]
    assert not result['ok'] and row['ai_s']==8 and row['ai_error']=='timed_out'
    assert row['speech_state']=='not_requested' and row['speech_s'] is None and calls==[]


def t_unknown_numbers_and_failed_cleanup_stop_the_benchmark_safely():
    calls=[]
    class Invalid:
        def explain(self,*args):return dict(explanation='Measured fuel is 999 litres.',evidence=['fuel.current_l'],claims=[])
    class Speech:
        def synthesize(self,*args):raise AssertionError('Invalid text reached speech')
    result=bench.measure(Invalid(),Speech(),'fixture',bench.sample(),attempts=1)
    assert result['attempts'][0]['ai_error']=='invalid_explanation'
    class Quarantined:
        def explain(self,*args):calls.append(1);raise bench.providers.ProviderError('cleanup_failed')
    result=bench.measure(Quarantined(),Speech(),'fixture',bench.sample(),attempts=2)
    assert len(result['attempts'])==1 and calls==[1] and result['attempts'][0]['ai_error']=='cleanup_failed'


def t_measurement_attempts_and_budgets_reject_unbounded_input_before_provider_work():
    class Blocked:
        def explain(self,*args):raise AssertionError('Invalid measurement reached a provider')
    for attempts in (True,0,3):
        try:bench.measure(Blocked(),None,'fixture',bench.sample(),attempts=attempts)
        except ValueError:pass
        else:raise AssertionError('Unbounded attempt count accepted')
    for budget in (True,7,61,float('nan'),float('inf')):
        try:bench.measure(Blocked(),None,'fixture',bench.sample(),ai_budget=budget)
        except ValueError:pass
        else:raise AssertionError('Unbounded measurement budget accepted')


def t_measurement_reports_completion_beyond_the_unchanged_live_budget():
    from test_driver_radio_providers import wav
    at=[0.0];budgets=[]
    class AI:
        def explain(self,agent,message,stopped,budget):
            budgets.append(budget);at[0]+=12
            return dict(explanation='The measurements may help.',evidence=['fuel.current_l'],claims=[])
    class Speech:
        def synthesize(self,*args):at[0]+=1;return wav()
    report=bench.measure(AI(),Speech(),'fixture',bench.sample(),attempts=1,ai_budget=30,clock=lambda:at[0])
    assert report['ok'] and report['ai_budget_s']==30 and report['live_ai_budget_s']==8 and budgets==[30]
    row=report['attempts'][0]
    assert row['ai_s']==12 and row['speech_s']==1 and row['time_to_audio_s']==13
    assert row['within_live_ai_budget'] is False and row['answer'].startswith('AI interpretation.')


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

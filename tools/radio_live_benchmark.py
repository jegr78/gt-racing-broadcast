#!/usr/bin/env python3
"""Measure existing LiveAI and speech adapters with explicit provider consent."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src/scripts'),str(ROOT/'tools')]
import logsetup
import driver_radio_ai as live
import driver_radio_providers as providers
from driver_radio_process import run_process

QUESTION='What can you infer from the supplied measurements?'


def sample(question=QUESTION):
    """Public synthetic input, never a recording or the producer's live data."""
    import driver_radio_questions
    if driver_radio_questions.plan(question)['rejected']:
        raise ValueError('Control commands are unavailable')
    return dict(kind='question',source='engineer',preset='status',answer_mode='live_ai',question=question,
        conversation=[],evidence=dict(fuel=dict(current_l=15,consumption_l=3,range_laps=5,usable_laps=5,reserve_l=0),
            pace=dict(delta_s=0,baseline_s=90,recent_s=90,last_s=90,best_s=90),
            tyres=dict(temperatures_c=[80,81,82,83]),laps=dict(fastest_s=90,fastest_lap=1,last_s=90,last_lap=6),
            uncertainties=['Traffic, weather and tyre wear are not measured.']))


def measure(ai,speech,agent,message,attempts=2,clock=time.monotonic,ai_budget=providers.BUDGET,speech_budget=providers.BUDGET,inspect=None):
    if type(attempts) is not int or not 1<=attempts<=2:raise ValueError('Use one or two attempts')
    if type(ai_budget) not in (int,float) or not providers.BUDGET<=ai_budget<=60:raise ValueError('Use an eight to sixty second measurement budget')
    stopped=threading.Event();rows=[]
    for index in range(attempts):
        row=dict(attempt=index+1,ai_state='pending',speech_state='not_requested',ai_s=None,speech_s=None)
        started=clock()
        try:
            result=ai.explain(agent,message,stopped,ai_budget)
            text=providers.ANSWER_PREFIX+providers.render_explanation(result,message)
            if len(text)>providers.LIVE_ANSWER_MAX_CHARS:raise providers.ProviderError('invalid_explanation')
            row.update(ai_state='ready',ai_s=round(clock()-started,4),within_live_ai_budget=clock()-started<=providers.LIVE_ANSWER_MAX_S,
                       speech_state='pending',answer=text)
            synthesis=clock();audio=speech.synthesize(text,stopped,speech_budget)
            row.update(speech_state='ready',speech_s=round(clock()-synthesis,4),audio_seconds=providers.validate_wav(audio))
        except providers.ProviderError as exc:
            stage='speech' if row['ai_state']=='ready' else 'ai'
            row.update({stage+'_state':'failed',stage+'_error':exc.code,stage+'_s':round(clock()-(synthesis if stage=='speech' else started),4)})
            if exc.code=='invalid_explanation' and inspect is not None:
                rejected=inspect()
                if rejected:row['rejected_answer']=rejected
            if exc.code=='cleanup_failed':
                row['time_to_audio_s']=round(clock()-started,4);rows.append(row);break
        row['time_to_audio_s']=round(clock()-started,4)
        rows.append(row)
    return dict(version=1,ok=all(r['speech_state']=='ready' for r in rows),facts_source='synthetic',
        question=message['question'],ai_budget_s=ai_budget,live_ai_budget_s=providers.LIVE_ANSWER_MAX_S,speech_budget_s=speech_budget,
        playback=False,attempts=rows)


def source_identity():
    paths=['src/scripts/driver_radio_ai.py','src/scripts/driver_radio_providers.py','src/scripts/driver_radio_process.py','src/scripts/driver_radio_service.py']
    return dict(python=platform.python_version(),platform=platform.platform(),
        source_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})


def offline_check(attempts=2,ai_budget=30):
    """Execute the measuring code with fake providers; no live timing claim."""
    calls={'agent':0,'speech':0}
    class AI:
        def explain(self,agent,message,stopped,budget):
            calls['agent']+=1
            return dict(explanation='The measurements may support a comparison.',evidence=['fuel.current_l'],
                claims=[dict(fact='fuel.current_l',value=15,unit='litres')])
    class Speech:
        def synthesize(self,text,stopped,budget):
            calls['speech']+=1;out=io.BytesIO()
            with wave.open(out,'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000);stream.writeframes(b'\0\0'*2400)
            return out.getvalue()
    return dict(measure(AI(),Speech(),'fixture',sample(),attempts,ai_budget=ai_budget),check=True,provider_calls=0,
                simulated_calls=calls,timing_origin='synthetic_check')


def main(argv=None):
    logsetup.harden_stdio()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Offline fixture check; no providers, devices or network')
    parser.add_argument('--machine-runtime',type=Path,help='Existing producer machine runtime directory')
    parser.add_argument('--agent',help='Existing isolated machine agent ID')
    parser.add_argument('--speech',choices=('kokoro','google'),help='Use the producer configured speech engine')
    parser.add_argument('--voice',default='am_michael',help='Configured Kokoro voice')
    parser.add_argument('--env-file',type=Path,help='Existing machine .env for the Google key; environment wins')
    parser.add_argument('--allow-agent',action='store_true',help='Allow up to two agent calls; sends synthetic facts and the question')
    parser.add_argument('--allow-cloud',action='store_true',help='Allow up to two potentially billed Google speech requests')
    parser.add_argument('--attempts',type=int,choices=(1,2),default=2)
    parser.add_argument('--agent-budget',type=float,default=30,help='Measurement-only agent deadline, 8 to 60 seconds; live radio keeps eight')
    parser.add_argument('--question',default=QUESTION,help='Bounded question with the documented synthetic facts')
    args=parser.parse_args(argv)
    if not providers.BUDGET<=args.agent_budget<=60:parser.error('Measurement budget must be between 8 and 60 seconds')
    if args.check:
        print(json.dumps(offline_check(args.attempts,args.agent_budget),ensure_ascii=True));return 0
    if not args.allow_agent or not args.machine_runtime or not args.agent or not args.speech:
        parser.error('Live measurement requires --allow-agent, --machine-runtime, --agent and --speech')
    if args.speech=='google' and not args.allow_cloud:parser.error('Google speech requires --allow-cloud')
    try:message=sample(args.question)  # Validate before provider construction or admission.
    except ValueError:
        parser.error('Use a bounded telemetry question')
        return 2
    import e2e_safety
    contained=e2e_safety.enter(__file__,list(sys.argv[1:] if argv is None else argv))
    if contained is not None:return contained
    timings=[]
    def process(invocation,prompt,stopped,budget,env):
        start=time.monotonic();row=dict(stage='probe' if prompt is None else 'answer',budget_s=budget)
        try:
            data=run_process(invocation,prompt,stopped,budget,env);row['state']='ready';return data
        except providers.ProviderError as exc:row.update(state='failed',error=exc.code);raise
        finally:row['elapsed_s']=round(time.monotonic()-start,4);timings.append(row)
    def key():
        from config import parse_env_text
        value=os.environ.get('RACECAST_GCLOUD_TTS_KEY','')
        if value or args.env_file is None:return value
        try:
            with args.env_file.open(encoding='utf8') as stream:raw=stream.read(131073)
        except (OSError,UnicodeError):return ''
        return parse_env_text(raw).get('RACECAST_GCLOUD_TTS_KEY','') if len(raw)<=131072 else ''
    # A rejected answer is recorded with its size, so a validator failure needs no extra call.
    last={};validate=providers.validate_explanation
    def recording(result,facts,**options):
        last.clear();last['result']=result;return validate(result,facts,**options)
    providers.validate_explanation=recording
    def inspect():
        result=last.get('result')
        if not isinstance(result,dict) or not isinstance(result.get('explanation'),str):return None
        claims=result.get('claims') if isinstance(result.get('claims'),list) else []
        rendered=sum(len(f"{providers.FACT_LABELS.get(c.get('fact'),'')}: {c.get('value')} {c.get('unit')}.")+1
                     for c in claims if isinstance(c,dict))
        return dict(explanation_chars=len(result['explanation']),claims=len(claims),
                    rendered_chars=len(result['explanation'])+rendered,explanation=result['explanation'][:600])
    speech_budget=providers.BUDGET
    warmup_s=None
    if args.speech=='kokoro':
        from driver_radio_kokoro import KokoroSpeech
        engine=KokoroSpeech(args.machine_runtime)
        class Speech:
            def synthesize(self,text,stopped,budget):return engine.synthesize(text,stopped,budget,voice=args.voice)
        speech=Speech();speech_budget=providers.LOCAL_SPEECH_BUDGET
        # The relay keeps the model loaded; measure synthesis, not the cold start.
        started=time.monotonic();engine.synthesize('Radio check.',threading.Event(),120,voice=args.voice)
        warmup_s=round(time.monotonic()-started,3)
    else:
        engine=providers.GoogleSpeech(key=key);speech=engine
    try:
        report=measure(live.LiveAI(args.machine_runtime,process=process,max_budget=args.agent_budget),speech,args.agent,
                       message,args.attempts,ai_budget=args.agent_budget,speech_budget=speech_budget,inspect=inspect)
        report['speech_warmup_s']=warmup_s
        report.update(check=False,timing_origin='live_adapter_measurement',agent_id=args.agent,speech_engine=args.speech,
                      speech_voice=args.voice if args.speech=='kokoro' else providers.VOICE,
                      process_runs=timings,source=source_identity())
    finally:
        if hasattr(engine,'close'):engine.close()
    print(json.dumps(report,ensure_ascii=True))
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())

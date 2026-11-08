#!/usr/bin/env python3
"""The session interview (#1088): one wake phrase, then one question per setting."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_native as native
import driver_radio_setup_voice as voice
from test_driver_radio_assessment import snapshot
from test_driver_radio_native import Harness
from test_driver_radio_service import Fixture


def t_the_interview_starts_in_english_and_german():
    for text in ('Hey Jeff, ask me for the session info','ask me about the session setup','session interview',
                 'Hey Jeff, frag mich nach den Session Infos','frag mich nach der Session','hey sheriff ask me for the session info'):
        assert voice.interview_start(text),text
    for text in ('what is the session','race','ask me later','session',None,'x'*401):
        assert not voice.interview_start(text),text


def t_the_interview_starts_from_a_misheard_request():
    # #1101: live transcriptions of "Hey Jeff, ask me for the session info".
    for text in ('hf asked me about the session info','me about the session and full',
                 'hey jeff asked me for the session in fall','asking me about the session','interview me on the session'):
        assert voice.interview_start(text),text
    for text in ('tell me about the session','what session is it','how long is the session','ask me later'):
        assert not voice.interview_start(text),text


def t_the_answer_grammar_covers_every_interview_answer():
    words=set(voice.interview_words())
    norm=lambda w:w.translate(str.maketrans({'ä':'ae','ö':'oe','ü':'ue','ß':'ss'}))
    known={norm(w) for w in words}
    for text in ('race','training','qualifying','it is the race','twelve laps','dreißig minuten','racing medium',
                 'rennreifen weich','two','keine','one mandatory stop','racing medium and racing hard','none',
                 'five litres','one point five','fünfundzwanzig runden','skip','weiter','stop','abbrechen',
                 'überspringen','intermediate','full wet','zero','nächste frage'):
        missing=[w for w in voice._clean(text).split() if w not in known]
        assert not missing,(text,missing)
    assert {'fünf','dreißig','zwölf'}<=words,'German model words carry their umlauts'
    assert all(w==w.lower() and ' ' not in w and w.isalpha() for w in words),'single lower-case words only'


def t_each_answer_uses_the_setup_vocabulary_without_its_keyword():
    for step,text,setup in (
            ('session','race',dict(session='race')),('session','Training',dict(session='practice')),
            ('length','12 laps',dict(laps=12,duration_min=None)),('length','dreißig Minuten',dict(duration_min=30,laps=None)),
            ('starting_compound','racing medium',dict(starting_compound='RM')),
            ('starting_compound','Rennreifen weich',dict(starting_compound='RS')),
            ('mandatory_stops','two',dict(mandatory_stops=2)),('mandatory_stops','keine',dict(mandatory_stops=0)),
            ('mandatory_stops','one mandatory stop',dict(mandatory_stops=1)),
            ('mandatory_compounds','racing medium and racing hard',dict(mandatory_compounds=['RM','RH'])),
            ('mandatory_compounds','none',dict(mandatory_compounds=[])),
            ('fuel_reserve','2 litres',dict(fuel_reserve_l=2.0)),('fuel_reserve','1,5',dict(fuel_reserve_l=1.5)),
            ('fuel_reserve','keine',dict(fuel_reserve_l=0.0))):
        kind,result=voice.interview_reply(step,text)
        assert kind=='set' and result['setup']==setup,(step,text,kind,result)
    for text,kind in (('skip','skip'),('weiter','skip'),('stop','stop'),('abbrechen','stop'),('banana','unclear'),('','unclear')):
        assert voice.interview_reply('session',text)[0]==kind,text
    assert voice.interview_reply('length','twelve')==('unclear',None)
    assert voice.interview_reply('session','12 laps')==('unclear',None)


def t_the_summary_reads_back_every_answer():
    assert voice.interview_summary(['Copy, race.','Copy, 12 laps.','Copy, reserve 2 litres.'])=='Copy, race, 12 laps, reserve 2 litres.'


def ask(f,binding,answer):
    return f.service.interview_answer(answer,binding)['text']


def t_a_full_interview_sets_every_answer_and_reads_them_back():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);state=f.poll();binding=state['binding']
        first=f.service.question('Hey Jeff, ask me for the session info',binding)
        assert first['text']=='Which session is it?' and first['preset']=='interview' and f.service.interview_waiting()
        assert ask(f,binding,'race')=='How long is the race, laps or minutes?'
        assert ask(f,binding,'12 laps')=='Which tyres do you start on?'
        assert ask(f,binding,'skip')=='How many mandatory stops?'
        assert ask(f,binding,'banana')=='Say again. How many mandatory stops?'
        assert ask(f,binding,'one')=='Which compounds are mandatory?'
        assert ask(f,binding,'racing hard')=='What fuel reserve?'
        assert ask(f,binding,'2 litres')=='Copy, race, 12 laps, 1 mandatory stop, mandatory racing hard, reserve 2 litres.'
        assert not f.service.interview_waiting()
        assert f.session.read(binding)['setup']==dict(session='race',laps=12,mandatory_stops=1,
                                                       mandatory_compounds=['RH'],fuel_reserve_l=2.0)
        assert f.poll()['epoch']==state['epoch'],'interview answers keep the radio session'
        # After the interview, an answer is a normal question again.
        assert f.service.interview_answer('race',binding) is None


def t_silence_repeats_the_question_once_then_ends():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);binding=f.poll()['binding']
        f.service.question('session interview',binding)
        assert f.service.interview_timeout(binding)['text']=='Say again. Which session is it?'
        assert f.service.interview_timeout(binding)['text']=='No answer. Interview ended. Nothing set.'
        assert not f.service.interview_waiting()
        f.service.question('session interview',binding)
        assert ask(f,binding,'qualifying')=='How long is the race, laps or minutes?'
        assert ask(f,binding,'stop')=='Interview stopped. Copy, qualifying.'
        assert not f.service.interview_waiting()


def t_the_interview_needs_a_session_and_ends_with_it():
    with tempfile.TemporaryDirectory() as d:
        snap=snapshot();snap['session_id']=None
        f=Fixture(d,snap);binding=f.poll()['binding']
        assert f.service.question('session interview',binding)['text']=='No telemetry session yet. Say it again once you are on track.'
        assert not f.service.interview_waiting()
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);binding=f.poll()['binding']
        f.service.question('session interview',binding)
        f.snap['context_revision']+=1;f.poll()
        assert not f.service.interview_waiting(),'a new radio epoch drops the open interview'


def speak_latest(h):
    """Play the newest queued radio line to its end, as the relay would."""
    message=h.f.service.history()['messages'][-1]
    h.f.service.attach(message['id'],message['epoch'],{'audio':b'wav','audio_state':'ready'})
    for _ in range(3):h.n.step()
    h.players[-1].done=True;h.n.step();h.f.at+=1
    for _ in range(3):h.n.step()


def t_native_voice_listens_without_wake_for_an_answer():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        assert h.args[-1]['listen_s'] is None,'wake-gated outside the interview'
        h.workers[0].events=[{'event':'question','text':'Hey Jeff, ask me for the session info'}]
        h.n.step()
        assert h.f.service.interview_waiting()
        workers=len(h.workers)
        speak_latest(h)
        config=[c for c in h.args if isinstance(c,dict)][-1]
        assert len(h.workers)==workers+1 and config['listen_s']==native.INTERVIEW_LISTEN_S==8,(len(h.workers),config)
        assert config['hold'] is True and h.workers[-1].released
        claimed=[];h.n._coaching=lambda text,signature:claimed.append(text) or True
        h.workers[-1].events=[{'event':'question','text':'race'}]
        h.n.step()
        assert claimed==[] and h.f.service.history()['messages'][-1]['text']=='How long is the race, laps or minutes?'
        speak_latest(h)
        h.workers[-1].events=[{'event':'error','code':'utterance_timeout'}]
        h.n.step()
        assert h.f.service.history()['messages'][-1]['text']=='Say again. How long is the race, laps or minutes?'
        assert h.n._retry_at<=h.f.at,'silence after a question needs no error back-off'
        h.n.close()


def t_a_failing_window_ends_the_interview_instead_of_reopening_the_microphone():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        h.workers[0].events=[{'event':'question','text':'session interview'}]
        h.n.step();speak_latest(h)
        assert [c for c in h.args if isinstance(c,dict)][-1]['listen_s']==native.INTERVIEW_LISTEN_S
        h.workers[-1].events=[{'event':'error','code':'microphone_unavailable'}]
        h.n.step()
        assert not h.f.service.interview_waiting()
        assert h.f.service.history()['messages'][-1]['text']=='Voice unavailable. Interview ended. Nothing set.'
        h.n.close()
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        h.workers[0].events=[{'event':'question','text':'session interview'}]
        h.n.step();speak_latest(h)
        h.f.at+=60;h.n.step()  # The armed worker stops reporting.
        assert not h.f.service.interview_waiting()
        h.n.close()
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        h.control(muted=True);h.n.step()
        h.workers[-1].events=[{'event':'question','text':'session interview'}]
        for _ in range(4):h.n.step()
        h.f.at+=1;h.n.step()
        assert not h.f.service.interview_waiting(),'muted replies never open a wake-free window'
        assert all(c['listen_s'] is None for c in h.args if isinstance(c,dict))
        h.n.close()


def t_an_uncertain_answer_is_asked_again_not_a_voice_failure():
    # #1101: a short answer below the confidence floor is an unclear answer.
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        h.workers[0].events=[{'event':'question','text':'session interview'}]
        h.n.step();speak_latest(h)
        h.workers[-1].events=[{'event':'error','code':'transcription_uncertain'}]
        h.n.step()
        assert h.f.service.interview_waiting(),'one unclear answer keeps the interview'
        assert h.f.service.history()['messages'][-1]['text']=='Say again. Which session is it?'
        assert h.n._retry_at<=h.f.at,'an unclear answer needs no error back-off'
        speak_latest(h)
        h.workers[-1].events=[{'event':'error','code':'transcription_uncertain'}]
        h.n.step()
        assert not h.f.service.interview_waiting()
        assert h.f.service.history()['messages'][-1]['text']=='No clear answer. Interview ended. Nothing set.'
        h.n.close()


def t_an_uncertain_wake_request_shows_what_was_heard():
    # #1101: the driver sees the transcription instead of silence or an error.
    import logging
    seen=[]
    class Grab(logging.Handler):
        def emit(self,record):seen.append(record.getMessage())
    handler=Grab();native.LOG.addHandler(handler);native.LOG.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as d:
            h=Harness(d);h.n.step()
            h.workers[0].events=[{'event':'error','code':'transcription_uncertain','heard':'hf asked me','confidence':.41}]
            h.n.step()
            messages=h.f.service.history()['messages']
            assert messages,'an unsure request gets an answer, not silence'
            assert messages[-1]['text']=='Say again.' and messages[-1]['question']=='hf asked me',messages[-1]
            assert h.n.status()['error'] is None and h.n._retry_at<=h.f.at,'not a voice failure'
            h.n.close()
    finally:native.LOG.removeHandler(handler)
    assert any("'hf asked me'" in line and '0.41' in line and 'wake request' in line for line in seen),seen


def t_the_answer_window_transcribes_with_the_interview_grammar():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step()
        assert h.args[-1]['grammar'] is None,'wake-gated questions keep the free transcription'
        first_line_args=len(h.args)
        h.workers[0].events=[{'event':'question','text':'session interview'}]
        h.n.step();speak_latest(h)
        answer=[c for c in h.args[first_line_args:] if isinstance(c,dict)][-1]
        assert answer['listen_s']==native.INTERVIEW_LISTEN_S and answer['grammar']==list(voice.interview_words()),            'the answer window must transcribe against the interview vocabulary'
        h.n.close()


def first_line(h):
    """Ask for the interview and start speaking its first question (player running)."""
    h.n.step()
    h.workers[0].events=[{'event':'question','text':'Hey Jeff, ask me for the session info'}]
    h.n.step()
    message=h.f.service.history()['messages'][-1]
    h.f.service.attach(message['id'],message['epoch'],{'audio':b'wav','audio_state':'ready'})
    h.n.step()
    assert len(h.players)==1 and not h.players[-1].done


def t_the_answer_window_opens_when_the_question_ends_not_seconds_later():
    # #1095: the worker needs ~3 s to load its models; spawned after the question it
    # missed an answer given right away. It now starts with the question, held.
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);first_line(h)
        held=h.workers[-1];config=[c for c in h.args if isinstance(c,dict)][-1]
        assert config['hold'] is True and config['listen_s']==native.INTERVIEW_LISTEN_S
        assert not held.released,'the engineer is still speaking: nothing may count as an answer'
        for _ in range(12):  # A long question: heartbeats keep coming and are drained.
            held.events=[{'event':'heartbeat'}];h.f.at+=1;h.n.step()
            assert not held.closed and held.events==[],'the held worker must survive the playback'
        workers=len(h.workers)
        h.players[-1].done=True;h.n.step()
        assert not held.released,'a short guard lets the speaker tail fade first'
        h.f.at+=native.INTERVIEW_RELEASE_GUARD_S;h.n.step()
        assert held.released and len(h.workers)==workers,'no second worker, no startup gap'
        assert h.n.status()['phase']=='listening'
        held.events=[{'event':'question','text':'race'}];h.n.step()
        assert h.f.service.history()['messages'][-1]['text']=='How long is the race, laps or minutes?'
        h.n.close()


def t_a_held_worker_that_fails_during_the_question_falls_back_to_a_fresh_one():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);first_line(h)
        held=h.workers[-1];held.events=[{'event':'error','code':'microphone_unavailable'}]
        h.n.step()
        assert held.closed and h.f.service.interview_waiting(),'the interview survives a lost pre-start'
        workers=len(h.workers)
        h.players[-1].done=True;h.n.step();h.f.at+=1;h.n.step()
        assert len(h.workers)==workers+1 and h.args[-1]['listen_s']==native.INTERVIEW_LISTEN_S
        assert h.args[-1]['hold'] is False,'spawned after the question: listening at once'
        h.n.close()


def t_only_interview_questions_start_capture_during_playback():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step();h.workers[0].events=[{'event':'question','text':'Fuel status?'}]
        h.n.step();message=h.f.service.history()['messages'][-1]
        h.f.service.attach(message['id'],message['epoch'],{'audio':b'wav','audio_state':'ready'})
        h.n.step();assert len(h.players)==1 and len(h.workers)==1 and h.workers[0].closed
        assert all(c['hold'] is False for c in h.args if isinstance(c,dict))
        h.n.close()


def t_the_worker_waits_for_the_release_line():
    import io,threading
    import driver_radio_voice_worker as worker
    for data,released in ((b'go\n',True),(b'',False),(b'later\n',False)):
        event=threading.Event()
        worker.wait_for_release(io.BytesIO(data),event)
        assert event.is_set() is released,data


def t_wake_free_text_is_kept_only_when_it_answers():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);binding=f.poll()['binding']
        f.service.question('session interview',binding)
        assert f.service.interview_answer('someone else talking',binding)['question'] is None
        assert f.service.interview_answer('race',binding)['question']=='race'


def t_the_wake_free_windows_are_capped():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);binding=f.poll()['binding']
        f.service.question('session interview',binding)
        f.service._interview['asked']=13
        assert not f.service.interview_waiting()
        f.service._interview['asked']=1
        f.service.close()
        assert not f.service.interview_waiting() and f.service._interview is None


def t_the_worker_accepts_only_a_bounded_answer_window():
    import driver_radio_voice_worker as worker
    body=dict(version=1,platform='linux',microphone='source.id',language='en',wake='hey_jeff',stt='vosk',
              paths={'library':'lib','en':'en','de':'de'},whisper_exe='',whisper_model='',google_key='',listen_s=None,
              hold=False,grammar=None)
    assert worker.validate(dict(body))['listen_s'] is None
    assert worker.validate(dict(body,listen_s=5))['listen_s']==5
    assert worker.validate(dict(body,listen_s=8,hold=True))['hold'] is True
    for bad in (dict(hold=True),dict(listen_s=5,hold=1),dict(listen_s=5,hold=None)):
        try:worker.validate(dict(body,**bad))
        except ValueError:continue
        raise AssertionError(bad)  # a held capture is only an armed answer window
    assert worker.validate(dict(body,listen_s=8,grammar=['race','zwei']))['grammar']==['race','zwei']
    for bad in (['race'],[],['Race'],['two words'],['x'*41],['race']*1001,'race',[1]):
        try:worker.validate(dict(body,listen_s=8 if bad!=['race'] else None,grammar=bad))
        except ValueError:continue
        raise AssertionError(bad)  # a grammar is a bounded word list, only for an answer window
    for bad in (0,11,'5',True,float('nan')):
        try:worker.validate(dict(body,listen_s=bad))
        except ValueError:continue
        raise AssertionError(bad)
    missing=dict(body);missing.pop('listen_s')
    try:worker.validate(missing)
    except ValueError:pass  # an old body without the field fails closed
    else:raise AssertionError('listen_s is required')


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

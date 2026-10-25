#!/usr/bin/env python3
"""No paid calls: bounded fake Google responses and grounded live attachments."""
import base64
import io
import json
import sys
import tempfile
import threading
import wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_providers as providers
from test_driver_radio_service import Fixture


def wav(seconds=.1):
    out=io.BytesIO()
    with wave.open(out,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000)
        stream.writeframes(b'\0\0'*int(24000*seconds))
    return out.getvalue()


class Response:
    def __init__(self,raw):self.raw=raw
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def read(self,n):return self.raw[:n]


def rejected(fn,code):
    try:fn()
    except providers.ProviderError as exc:assert exc.code==code,(exc.code,code)
    else:raise AssertionError('Expected '+code)


def t_google_request_voice_headers_bounds_and_exact_cache():
    calls=[]
    def request(url,**kwargs):
        calls.append((url,kwargs))
        return Response(json.dumps({'audioContent':base64.b64encode(wav()).decode()}).encode())
    speech=providers.GoogleSpeech(key=lambda:'test-key',open_url=request)
    first=speech.synthesize('Fuel status is available.',threading.Event(),8)
    assert first==wav() and speech.synthesize('Fuel status is available.',threading.Event(),8)==first
    assert len(calls)==1
    url,args=calls[0]
    body=json.loads(args['data'])
    assert 'test-key' not in url and args['headers']['X-Goog-Api-Key']=='test-key'
    assert body['voice']=={'languageCode':'en-US','name':'en-US-Studio-Q'}
    assert body['input']=={'text':'Fuel status is available.'}
    assert body['audioConfig']['audioEncoding']=='LINEAR16'
    assert args['allow_redirects'] is False and 0 < args['timeout'] <= 8


def t_google_failures_are_safe_and_never_substitute_a_voice():
    calls=[]
    def unexpected(*args,**kwargs):calls.append(1);raise AssertionError('Unexpected HTTP')
    rejected(lambda:providers.GoogleSpeech(key=lambda:'',open_url=unexpected).synthesize('Fuel',threading.Event(),8),'unconfigured')
    assert calls==[]
    for raw,code in ((b'{}','invalid_audio'),(b'{','invalid_audio'),
                     (json.dumps({'audioContent':'not base64'}).encode(),'invalid_audio'),
                     (json.dumps({'audioContent':base64.b64encode(b'not a wav').decode()}).encode(),'invalid_audio'),
                     (b'x'*(providers.JSON_MAX+1),'response_too_large'),
                     (json.dumps({'audioContent':base64.b64encode(wav(26)).decode()}).encode(),'audio_too_long')):
        speech=providers.GoogleSpeech(key=lambda:'private-key',open_url=lambda *args,raw=raw,**kwargs:Response(raw))
        rejected(lambda speech=speech:speech.synthesize('Fuel',threading.Event(),8),code)
    stopped=threading.Event();stopped.set()
    rejected(lambda:providers.GoogleSpeech(key=lambda:'key',open_url=unexpected).synthesize('Fuel',stopped,8),'cancelled')


def t_grounded_explanation_rejects_unsupported_facts_and_numbers():
    facts={'fuel.usable_laps':{'value':2.5,'unit':'laps'},'pace.delta_s':{'value':1,'unit':'seconds'}}
    valid={'explanation':'Pace is one second slower. Tyres may contribute; traffic is unknown.',
           'evidence':['pace.delta_s'],'claims':[{'fact':'pace.delta_s','value':1,'unit':'seconds'}]}
    assert providers.validate_explanation(valid,facts).startswith('Pace')
    for change in ({'explanation':'The tyres have 40 percent wear.'},
                   {'claims':[{'fact':'pace.delta_s','value':2,'unit':'seconds'}]},
                   {'evidence':['unknown.fact']},{'explanation':'Traffic caused the slowdown.'},
                   {'explanation':'Box now.'},{'claims':[{'fact':'pace.delta_s','value':1,'unit':'litres'}]}):
        rejected(lambda change=change:providers.validate_explanation(dict(valid,**change),facts),'invalid_explanation')


def t_provider_work_is_text_first_and_late_results_cannot_attach():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d)
        f.access.update({'expected':1,'providers':{'speech':True,'agent':None}});f.poll()
        message=f.request()
        assert message['text'] and message['audio_state']=='pending'
        entered,release=threading.Event(),threading.Event()
        class Speech:
            def synthesize(self,text,stopped,budget):entered.set();assert release.wait(3);return wav()
        worker=providers.Enricher(f.service,speech=Speech())
        thread=threading.Thread(target=lambda:worker.step('audio'))
        thread.start();assert entered.wait(3)
        assert f.service.history()['messages'][-1]['text']==message['text']
        f.snap['context_revision']+=1;f.poll();release.set();thread.join(3)
        assert not thread.is_alive() and f.service.history()['messages']==[]
        assert f.service.audio_for('forged') is None
        worker.close()


def t_unconfigured_services_and_voice_disable_make_no_calls():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();message=f.request()
        class Speech:
            def synthesize(self,*args):raise AssertionError('No speech consent')
        worker=providers.Enricher(f.service,speech=Speech())
        worker.step('audio');worker.step('ai')
        current=f.service.history()['messages'][-1]
        assert current['id']==message['id'] and current['audio_state']=='unconfigured'
        assert current['ai_state']=='unconfigured'
        worker.close()


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

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
    def __init__(self,raw):self.stream=io.BytesIO(raw)
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def read(self,n):return self.stream.read(n)
    def read1(self,n):return self.stream.read(n)


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
    rejected(lambda:speech.synthesize('Fuel status is available.',threading.Event(),0),'timed_out')
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
                   {'explanation':'Pace is 1e3 seconds slower. Tyres may contribute.'},
                   {'explanation':'The fuel range is infinite.'},
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


def t_stream_deadline_is_absolute_even_when_bytes_keep_arriving():
    class Clock:
        at=0
        def __call__(self):return self.at
    clock=Clock();reads=[]
    class Trickle:
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def read(self,n):raise AssertionError('Bulk read cannot enforce a streaming deadline')
        def read1(self,n):clock.at+=.6;reads.append(n);return b'x'
    speech=providers.GoogleSpeech(key=lambda:'key',open_url=lambda *args,**kwargs:Trickle(),clock=clock)
    rejected(lambda:speech.synthesize('Fuel',threading.Event(),8),'timed_out')
    assert len(reads)<=14 and all(n<=8192 for n in reads)


def t_enrichment_loop_recovers_after_a_transient_settings_error():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);worker=providers.Enricher(f.service)
        calls=[];errors=[]
        def step(kind):
            calls.append(kind)
            if len(calls)==1:raise ValueError('temporary settings read failure')
            worker._stop.set()
        worker.step=step
        def run():
            try:worker._run('audio')
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=run);thread.start();thread.join(3)
        assert not thread.is_alive() and len(calls)==2 and not errors, (calls,errors)


def t_cache_eviction_and_failure_messages_remain_bounded_and_private():
    from unittest.mock import patch
    calls=[]
    def request(*args,**kwargs):
        calls.append(1)
        return Response(json.dumps({'audioContent':base64.b64encode(wav()).decode()}).encode())
    speech=providers.GoogleSpeech(key=lambda:'private-key',open_url=request)
    with patch.object(providers,'CACHE_MAX',len(wav())*2):
        for text in ('Fuel one','Fuel two','Fuel three'):speech.synthesize(text,threading.Event(),8)
        speech.synthesize('Fuel one',threading.Event(),8)
        assert len(calls)==4 and speech._bytes<=providers.CACHE_MAX
    def fails(*args,**kwargs):raise OSError('private-key in remote diagnostics')
    try:providers.GoogleSpeech(key=lambda:'private-key',open_url=fails).synthesize('Fuel',threading.Event(),8)
    except providers.ProviderError as exc:
        assert exc.code=='speech_failed' and 'private-key' not in str(exc)
    else:raise AssertionError('Expected safe provider failure')


def t_real_http_trickle_is_stopped_before_the_server_finishes():
    import http.server
    import time
    import http_util
    finished=threading.Event();sent=threading.Event()
    payload=json.dumps({'audioContent':base64.b64encode(wav()).decode()}).encode()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):return None
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers()
            try:
                for byte in payload[:20]:
                    self.wfile.write(bytes([byte]));self.wfile.flush();sent.set();time.sleep(.1)
                finished.set();self.wfile.write(payload[20:])
            except OSError:
                return  # Client deadline closed the response before all bytes arrived.
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    def open_url(url,**kwargs):
        return http_util.open_url('http://127.0.0.1:'+str(server.server_port)+'/',**kwargs)
    try:
        speech=providers.GoogleSpeech(key=lambda:'fixture-key',open_url=open_url)
        rejected(lambda:speech.synthesize('Fuel',threading.Event(),.4),'timed_out')
        assert sent.is_set() and not finished.is_set(), 'deadline must stop an unfinished transfer'
    finally:server.shutdown();server.server_close()


def t_configured_smoke_requires_explicit_cloud_consent_and_measures_real_calls():
    calls=[]
    def request(*args,**kwargs):
        calls.append(1)
        return Response(json.dumps({'audioContent':base64.b64encode(wav()).decode()}).encode())
    def factory():return providers.GoogleSpeech(key=lambda:'fixture-key',open_url=request)
    rejected(lambda:providers.smoke(False,speech_factory=factory),'consent_required')
    assert calls==[]
    result=providers.smoke(True,speech_factory=factory)
    assert result['voice']=='en-US-Studio-Q' and result['network_calls']==2 and len(calls)==2
    assert result['audio_seconds']>0 and all(result[k]>=0 for k in ('cold_s','warm_s','cached_s'))


def t_operator_cli_routes_smoke_and_does_not_call_cloud_without_flag():
    import contextlib
    from unittest.mock import patch
    import test_racecast
    m=test_racecast.m
    assert m.route(['radio','smoke'])=={'kind':'service','command':'radio','verb':'smoke','rest':[]}
    assert 'radio smoke --allow-cloud' in m.usage_for('radio')
    with patch.object(providers,'smoke') as check:
        try:m.radio_smoke_cmd([])
        except SystemExit as exc:assert exc.code!=0
        else:raise AssertionError('Cloud smoke must require explicit consent')
        check.assert_not_called()
        check.return_value={'voice':'en-US-Studio-Q','network_calls':2}
        out=io.StringIO()
        with contextlib.redirect_stdout(out):m.radio_smoke_cmd(['--allow-cloud'])
        assert json.loads(out.getvalue())['network_calls']==2
        check.assert_called_once_with(True,key=m._radio_tts_key)


def t_fast_audio_chunks_do_not_multiply_full_snapshot_preparation():
    import copy
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.access.update({'expected':1,'providers':{'speech':True,'agent':None}});f.poll();f.request()
        calls=[]
        def source():calls.append(1);return copy.deepcopy(f.snap)
        f.service.source=source
        class Chunks(Response):
            def read1(self,n):return self.stream.read(min(128,n))
        def request(*args,**kwargs):
            return Chunks(json.dumps({'audioContent':base64.b64encode(wav()).decode()}).encode())
        worker=providers.Enricher(f.service,speech=providers.GoogleSpeech(key=lambda:'key',open_url=request),clock=lambda:f.at)
        assert worker.step('audio')
        assert len(calls)<=5, 'network chunks must not trigger independent full telemetry preparations: '+str(len(calls))


def t_source_cli_uses_the_established_machine_key_with_environment_precedence():
    import os
    from unittest.mock import patch
    import test_racecast
    m=test_racecast.m
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)/'.env';path.write_text('RACECAST_GCLOUD_TTS_KEY=file-fixture-key\n',encoding='utf8')
        with patch.object(m,'_env_file',return_value=str(path)),patch.dict(os.environ,{'RACECAST_GCLOUD_TTS_KEY':''}):
            assert m._radio_tts_key()=='file-fixture-key'
            with patch.dict(os.environ,{'RACECAST_GCLOUD_TTS_KEY':'env-fixture-key'}):
                assert m._radio_tts_key()=='env-fixture-key'
            path.write_text('RACECAST_GCLOUD_TTS_KEY=oversized'+('x'*131073),encoding='utf8')
            assert m._radio_tts_key()==''


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

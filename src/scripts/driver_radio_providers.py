"""Optional bounded Studio-Q synthesis and grounded live explanation attachments."""

import base64
import collections
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path
import threading
import time
import wave


VOICE = 'en-US-Studio-Q'
URL = 'https://texttospeech.googleapis.com/v1/text:synthesize'
JSON_MAX = 3*1024*1024
AUDIO_MAX = 2*1024*1024
CACHE_MAX = 8*1024*1024
CACHE_ENTRIES = 16
BUDGET = 8.0


class ProviderError(ValueError):
    def __init__(self,code,message='Radio provider is unavailable'):
        self.code=code
        super().__init__(message)


def validate_wav(audio):
    if not isinstance(audio,bytes) or len(audio)>AUDIO_MAX:
        raise ProviderError('response_too_large')
    try:
        with wave.open(io.BytesIO(audio),'rb') as stream:
            if (stream.getcomptype()!='NONE' or stream.getnchannels()!=1
                    or stream.getsampwidth()!=2 or not 8000<=stream.getframerate()<=48000):
                raise ProviderError('invalid_audio')
            duration=stream.getnframes()/stream.getframerate()
            if duration<=0:raise ProviderError('invalid_audio')
            if duration>25:raise ProviderError('audio_too_long')
            if len(stream.readframes(stream.getnframes())) != stream.getnframes()*2:
                raise ProviderError('invalid_audio')
    except (wave.Error,EOFError,OSError,ValueError) as exc:
        if isinstance(exc,ProviderError):raise
        raise ProviderError('invalid_audio') from exc
    return duration


class GoogleSpeech:
    def __init__(self,key=None,open_url=None,clock=time.monotonic,runner=None):
        self.key=key or (lambda:os.environ.get('RACECAST_GCLOUD_TTS_KEY',''))
        self.open_url,self.clock,self.runner=open_url,clock,runner
        self._lock=threading.Lock()
        self._cache=collections.OrderedDict()
        self._bytes=0

    def synthesize(self,text,stopped,budget):
        deadline=self.clock()+min(BUDGET,budget)
        with self._lock:
            if stopped.is_set():raise ProviderError('cancelled')
            if self.clock()>=deadline:raise ProviderError('timed_out')
            key=self.key()
            if not key:raise ProviderError('unconfigured')
            if not isinstance(text,str) or not text.strip() or len(text)>1200:
                raise ProviderError('invalid_text')
            ident=hashlib.sha256((VOICE+'\0'+text).encode()).hexdigest()
            if ident in self._cache:
                self._cache.move_to_end(ident)
                return self._cache[ident]
            timeout=deadline-self.clock()
            if timeout<=0:raise ProviderError('timed_out')
            if self.open_url is None:
                raw=self._process_request(text,key,stopped,deadline)
            else:
                raw=self._direct_request(text,key,stopped,deadline)
            if len(raw)>JSON_MAX:raise ProviderError('response_too_large')
            if stopped.is_set():raise ProviderError('cancelled')
            if self.clock()>=deadline:raise ProviderError('timed_out')
            try:
                doc=json.loads(raw)
                if isinstance(doc,dict) and 'error' in doc:
                    code=doc['error']
                    safe=('unconfigured','cancelled','timed_out','response_too_large','invalid_audio','audio_too_long','speech_failed','invalid_request')
                    raise ProviderError(code if isinstance(code,str) and code in safe else 'speech_failed')
                encoded=doc['audioContent']
                if not isinstance(encoded,str):raise ValueError('audio')
                audio=base64.b64decode(encoded,validate=True)
            except ProviderError:
                raise
            except (ValueError,KeyError,TypeError,RecursionError) as exc:
                raise ProviderError('invalid_audio') from exc
            validate_wav(audio)
            self._cache[ident]=audio;self._bytes+=len(audio)
            while len(self._cache)>CACHE_ENTRIES or self._bytes>CACHE_MAX:
                _,old=self._cache.popitem(last=False);self._bytes-=len(old)
            return audio

    def _process_request(self,text,key,stopped,deadline):
        import ai_agents
        import ai_jobs
        from driver_radio_process import run_process
        command=([sys.executable,'_radio-speech-worker'] if getattr(sys,'frozen',False)
                 else [sys.executable,str(Path(__file__).with_name('driver_radio_tts_worker.py'))])
        extra={k:os.environ[k] for k in ('SSL_CERT_FILE','SSL_CERT_DIR') if os.environ.get(k)}
        if getattr(sys,'frozen',False):extra['PYINSTALLER_RESET_ENVIRONMENT']='1'
        with tempfile.TemporaryDirectory(prefix='racecast-radio-tts-') as scratch:
            left=deadline-self.clock()
            if left<=0:raise ProviderError('timed_out')
            invocation=ai_agents.Invocation(command,scratch,'',8,'speech')
            prompt=json.dumps(dict(key=key,text=text,budget=min(BUDGET,left),allow_cloud=True)).encode()
            try:
                return (self.runner or run_process)(invocation,prompt,stopped,left,
                    ai_jobs.execution_env(os.environ),output_limit=JSON_MAX,extra_env=extra)
            except ProviderError as exc:
                if exc.code in ('timed_out','cancelled','output_too_large','cleanup_failed'):raise
                raise ProviderError('speech_failed') from exc

    def _direct_request(self,text,key,stopped,deadline):
        timeout=deadline-self.clock()
        if timeout<=0:raise ProviderError('timed_out')
        body=dict(input={'text':text},voice={'languageCode':'en-US','name':VOICE},
                  audioConfig={'audioEncoding':'LINEAR16','sampleRateHertz':24000})
        try:
            with self.open_url(URL,data=json.dumps(body).encode(),method='POST',
                               headers={'Content-Type':'application/json','X-Goog-Api-Key':key},
                               timeout=timeout,allow_redirects=False) as response:
                raw=bytearray()
                read=getattr(response,'read1',response.read)
                while True:
                    if stopped.is_set():raise ProviderError('cancelled')
                    left=deadline-self.clock()
                    if left<=0:raise ProviderError('timed_out')
                    sock=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
                    if sock is not None:sock.settimeout(left)
                    block=read(min(8192,JSON_MAX+1-len(raw)))
                    if not block:break
                    raw.extend(block)
                    if len(raw)>JSON_MAX:raise ProviderError('response_too_large')
        except ProviderError:
            raise
        except TimeoutError:
            raise ProviderError('timed_out') from None
        except Exception as exc:
            raise ProviderError('speech_failed') from exc
        return bytes(raw)


# Numerical measurements are rendered from validated claims, never model prose.
NUMBER_RE=re.compile(r'\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|'
                     r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|'
                     r'nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|'
                     r'hundred|thousand|million|billion|half|quarter|double|triple|dozen)\b',re.I)
FACT_LABELS = {
    'fuel.current_l':'Current fuel', 'fuel.consumption_l':'Measured fuel per lap',
    'fuel.reserve_l':'Fuel reserve', 'fuel.range_laps':'Physical fuel range',
    'fuel.usable_laps':'Fuel range after reserve', 'pace.delta_s':'Recent pace change',
    'pace.baseline_s':'Baseline lap time', 'pace.recent_s':'Recent lap time',
    'pace.last_s':'Last eligible lap time', 'pace.best_s':'Best eligible lap time',
    'tyres.front_left':'Front left tyre temperature', 'tyres.front_right':'Front right tyre temperature',
    'tyres.rear_left':'Rear left tyre temperature', 'tyres.rear_right':'Rear right tyre temperature',
}


def compact_facts(message):
    evidence=message.get('evidence') or {}
    facts={}
    for section,keys,unit in (('fuel',('current_l','consumption_l','reserve_l'),'litres'),
                              ('fuel',('range_laps','usable_laps'),'laps'),
                              ('pace',('delta_s','baseline_s','recent_s','last_s','best_s'),'seconds')):
        for key in keys:
            value=evidence.get(section,{}).get(key)
            if type(value) in (int,float) and -1e9<=value<=1e9:
                facts[section+'.'+key]={'value':value,'unit':unit}
    for key,value in zip(('front_left','front_right','rear_left','rear_right'),
                         evidence.get('tyres',{}).get('temperatures_c',[None]*4),strict=True):
        if type(value) in (int,float) and -1000<=value<=1000:
            facts['tyres.'+key]={'value':value,'unit':'degrees_c'}
    return facts


def validate_explanation(result,facts):
    if not isinstance(result,dict) or set(result)!={'explanation','evidence','claims'}:
        raise ProviderError('invalid_explanation')
    text=result['explanation']
    refs,claims=result['evidence'],result['claims']
    if (not isinstance(text,str) or not text.strip() or len(text)>600
            or any(ord(c)<32 for c in text) or re.search(r'https?://|[<>]',text)
            or not isinstance(refs,list) or not 1<=len(refs)<=8
            or any(not isinstance(k,str) or k not in facts for k in refs)
            or not isinstance(claims,list) or len(claims)>8):
        raise ProviderError('invalid_explanation')
    rendered=[]
    for claim in claims:
        if not isinstance(claim,dict) or set(claim)!={'fact','value','unit'}:
            raise ProviderError('invalid_explanation')
        fact=facts.get(claim['fact']) if isinstance(claim['fact'],str) else None
        if (fact is None or claim['fact'] not in refs or type(claim['value']) not in (int,float)
                or claim['value']!=fact['value'] or claim['unit']!=fact['unit']):
            raise ProviderError('invalid_explanation')
        label=FACT_LABELS.get(claim['fact'])
        if label is None:raise ProviderError('invalid_explanation')
        rendered.append(f"{label}: {claim['value']:g} {claim['unit']}.")
    if NUMBER_RE.search(text):raise ProviderError('invalid_explanation')
    if re.search(r'\b(?:nan|infinite|infinity|percent|ideal|optimal)\b|%|\d\s*[+*/=]\s*\d',text,re.I):
        raise ProviderError('invalid_explanation')
    if re.search(r'\b(?:box now|pit now|must pit|must box)\b',text,re.I):
        raise ProviderError('invalid_explanation')
    uncertain=re.search(r'\b(?:may|could|might|unknown|unavailable|uncertain|consider)\b|not measured',text,re.I)
    if re.search(r'\b(?:traffic|weather|tyres?|tires?|wear|temperature)\b',text,re.I) and not uncertain:
        raise ProviderError('invalid_explanation')
    if re.search(r'\b(?:traffic|weather|tyres?|tires?|wear)\s+(?:caused|causes|is causing|are worn)|caused by (?:traffic|weather)',text,re.I):
        raise ProviderError('invalid_explanation')
    rendered_text=' '.join([text.strip(),*rendered])
    if len(rendered_text)>600:raise ProviderError('invalid_explanation')
    return rendered_text


class _Current:
    def __init__(self,worker,message):
        self.worker,self.message=worker,message
        self._checked=float('-inf')
        self._invalid=False
    def is_set(self):
        if self.worker._stop.is_set() or self._invalid:return True
        now=self.worker.clock()
        if now-self._checked>=.25:
            self._invalid=self.worker.service.remaining_for(self.message['id'],self.message['epoch'])<=0
            self._checked=now
        return self._invalid


class Enricher:
    def __init__(self,service,speech=None,ai=None,clock=time.monotonic):
        self.service=service
        self.speech=speech or GoogleSpeech()
        self.ai=ai
        self.clock=clock
        self._stop=threading.Event()
        self._threads=[]
        self._ai_at=float('-inf')
        self._step_locks={kind:threading.Lock() for kind in ('audio','ai')}

    def start(self):
        if self._threads or self._stop.is_set():return
        for kind in ('audio','ai'):
            thread=threading.Thread(target=self._run,args=(kind,),name='radio-'+kind,daemon=True)
            self._threads.append(thread);thread.start()

    def _run(self,kind):
        while not self._stop.is_set():
            try:self.step(kind)
            except (OSError,ValueError,TypeError):
                self._stop.wait(.1)
                continue  # Temporary unreadable profile settings do not kill the worker.
            self._stop.wait(.1)

    def step(self,kind):
        with self._step_locks[kind]:
            work=self.service.enrichment_work(kind)
            if work is None or self._stop.is_set():return False
            message=work
            stopped=_Current(self,message)
            state_key='audio_state' if kind=='audio' else 'ai_state'
            error_key='audio_error' if kind=='audio' else 'ai_error'
            try:
                config=self.service.settings.read()['providers']
                if kind=='audio':
                    if not config['speech'] or not self.service.refresh()['voice_enabled']:
                        raise ProviderError('unconfigured')
                    audio=self.speech.synthesize(message['text'],stopped,
                        min(BUDGET,self.service.remaining_for(message['id'],message['epoch'])))
                    fields={'audio':audio,state_key:'ready',error_key:None}
                else:
                    if not config['agent'] or self.ai is None:
                        raise ProviderError('unconfigured')
                    if message['severity']=='urgent':
                        return self.service.attach(message['id'],message['epoch'],{state_key:'not_requested'})
                    if self.clock()-self._ai_at<30:raise ProviderError('rate_limited')
                    self._ai_at=self.clock()
                    result=self.ai.explain(config['agent'],message,stopped,
                        min(BUDGET,self.service.remaining_for(message['id'],message['epoch'])))
                    explanation=validate_explanation(result,compact_facts(message))
                    fields={'explanation':explanation,state_key:'ready',error_key:None}
                if stopped.is_set():return False
            except ProviderError as exc:
                fields={state_key:'unconfigured' if exc.code=='unconfigured' else 'failed',error_key:exc.code}
            except Exception:
                fields={state_key:'failed',error_key:'provider_failed'}
            return self.service.attach(message['id'],message['epoch'],fields)

    def close(self):
        self._stop.set()
        for thread in self._threads:thread.join(2)


def smoke(allow_cloud=False,speech_factory=None,clock=time.monotonic,key=None):
    """Two deliberately authorized provider calls, then one in-memory cache read."""
    if not allow_cloud:raise ProviderError('consent_required')
    speech_factory=speech_factory or (lambda:GoogleSpeech(key=key))
    text='Racecast driver radio speech check.'
    stopped=threading.Event()
    first=speech_factory();start=clock()
    audio=first.synthesize(text,stopped,BUDGET);cold=clock()-start
    second=speech_factory();start=clock()
    second.synthesize(text,stopped,BUDGET);warm=clock()-start
    start=clock();second.synthesize(text,stopped,BUDGET);cached=clock()-start
    return dict(voice=VOICE,network_calls=2,audio_seconds=validate_wav(audio),
                cold_s=round(cold,4),warm_s=round(warm,4),cached_s=round(cached,4))

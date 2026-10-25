"""Optional bounded Studio-Q synthesis and grounded live explanation attachments."""

import base64
import collections
import hashlib
import io
import json
import os
import re
import threading
import time
import wave

import http_util

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
    def __init__(self,key=None,open_url=http_util.open_url,clock=time.monotonic):
        self.key=key or (lambda:os.environ.get('RACECAST_GCLOUD_TTS_KEY',''))
        self.open_url,self.clock=open_url,clock
        self._lock=threading.Lock()
        self._cache=collections.OrderedDict()
        self._bytes=0

    def synthesize(self,text,stopped,budget):
        deadline=self.clock()+min(BUDGET,budget)
        with self._lock:
            if stopped.is_set():raise ProviderError('cancelled')
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
            body=dict(input={'text':text},voice={'languageCode':'en-US','name':VOICE},
                      audioConfig={'audioEncoding':'LINEAR16','sampleRateHertz':24000})
            try:
                with self.open_url(URL,data=json.dumps(body).encode(),method='POST',
                                   headers={'Content-Type':'application/json','X-Goog-Api-Key':key},
                                   timeout=timeout,allow_redirects=False) as response:
                    raw=response.read(JSON_MAX+1)
            except Exception as exc:
                raise ProviderError('speech_failed') from exc
            if len(raw)>JSON_MAX:raise ProviderError('response_too_large')
            if stopped.is_set():raise ProviderError('cancelled')
            if self.clock()>=deadline:raise ProviderError('timed_out')
            try:
                encoded=json.loads(raw)['audioContent']
                if not isinstance(encoded,str):raise ValueError('audio')
                audio=base64.b64decode(encoded,validate=True)
            except (ValueError,KeyError,TypeError,RecursionError) as exc:
                raise ProviderError('invalid_audio') from exc
            validate_wav(audio)
            self._cache[ident]=audio;self._bytes+=len(audio)
            while len(self._cache)>CACHE_ENTRIES or self._bytes>CACHE_MAX:
                _,old=self._cache.popitem(last=False);self._bytes-=len(old)
            return audio


NUMBER_WORDS=dict(zip(('zero','one','two','three','four','five','six','seven','eight','nine','ten'),range(11),strict=True))
NUMBER_RE=re.compile(r'(?<![A-Za-z0-9_])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?![A-Za-z_])')


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
    values=[]
    for claim in claims:
        if not isinstance(claim,dict) or set(claim)!={'fact','value','unit'}:
            raise ProviderError('invalid_explanation')
        fact=facts.get(claim['fact']) if isinstance(claim['fact'],str) else None
        if (fact is None or claim['fact'] not in refs or type(claim['value']) not in (int,float)
                or claim['value']!=fact['value'] or claim['unit']!=fact['unit']):
            raise ProviderError('invalid_explanation')
        values.append(claim['value'])
    numbers=[float(m.group()) for m in NUMBER_RE.finditer(text)]
    numbers += [NUMBER_WORDS[word.lower()] for word in re.findall(r'\b(?:'+ '|'.join(NUMBER_WORDS)+r')\b',text,re.I)]
    if any(value not in values for value in numbers):raise ProviderError('invalid_explanation')
    if re.search(r'\b(?:box now|pit now|must pit|must box)\b',text,re.I):
        raise ProviderError('invalid_explanation')
    uncertain=re.search(r'\b(?:may|could|might|unknown|unavailable|uncertain|consider)\b|not measured',text,re.I)
    if re.search(r'\b(?:traffic|weather|tyres?|tires?|wear|temperature)\b',text,re.I) and not uncertain:
        raise ProviderError('invalid_explanation')
    if re.search(r'\b(?:traffic|weather|tyres?|tires?|wear)\s+(?:caused|causes|is causing|are worn)|caused by (?:traffic|weather)',text,re.I):
        raise ProviderError('invalid_explanation')
    return text.strip()


class _Current:
    def __init__(self,worker,message):self.worker,self.message=worker,message
    def is_set(self):
        return self.worker._stop.is_set() or self.worker.service.remaining_for(self.message['id'],self.message['epoch'])<=0


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
            self.step(kind)
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

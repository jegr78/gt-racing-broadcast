"""Local prepared wake recognition and bounded post-wake transcription adapters."""
import base64
import ctypes
import io
import json
import math
import os
import struct
import threading
import wave
from pathlib import Path

import ai_agents
import ai_jobs
import http_util
from driver_radio_process import run_process,temporary_directory
from driver_radio_voice_config import PHRASES

PCM_MAX=16*32000
PRE_ROLL=2*32000


def capture_args(platform,microphone):
    if not isinstance(microphone,str) or not microphone or len(microphone)>256 or any(ord(c)<32 for c in microphone):
        raise ValueError('microphone_unavailable')
    if platform.startswith('win'):source=['-f','dshow','-i','audio='+microphone]
    elif platform=='darwin':source=['-f','avfoundation','-i',':'+microphone]
    elif platform.startswith('linux'):source=['-f','pulse','-i',microphone]
    else:raise ValueError('microphone_unavailable')
    return ['-hide_banner','-loglevel','error','-nostdin',*source,'-vn','-ac','1','-ar','16000','-f','s16le','pipe:1']


class Utterance:
    def __init__(self):self.buffer=bytearray();self.active=False;self.quiet=0;self.after=0
    def feed(self,pcm,woke):
        if not isinstance(pcm,bytes) or len(pcm)%2 or len(pcm)>4096:raise ValueError('Invalid PCM chunk')
        self.buffer.extend(pcm)
        if not self.active:
            del self.buffer[:-PRE_ROLL]
            if not woke:return None
            self.active=True;self.after=0;self.quiet=0
        self.after+=len(pcm)
        samples=struct.unpack('<'+'h'*(len(pcm)//2),pcm)
        rms=math.sqrt(sum(v*v for v in samples)/max(1,len(samples)))
        self.quiet=self.quiet+len(pcm) if rms<200 else 0
        if len(self.buffer)>PCM_MAX:
            self.buffer.clear();self.active=False;return b''
        if self.after>=16000 and self.quiet>=25600:
            result=bytes(self.buffer);self.buffer.clear();self.active=False;return result
        return None


def wav_bytes(pcm):
    if not isinstance(pcm,bytes) or not 0<len(pcm)<=PCM_MAX or len(pcm)%2:raise ValueError('Invalid utterance')
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
    return out.getvalue()


class Vosk:
    def __init__(self,library,model,phrase=None):
        self.model=None;self.rec=None;self._dll=None
        if os.name=='nt':self._dll=os.add_dll_directory(str(Path(library).parent.resolve()))
        self.lib=ctypes.CDLL(str(library))
        for name,args,result in (
            ('vosk_set_log_level',[ctypes.c_int],None),
            ('vosk_model_new',[ctypes.c_char_p],ctypes.c_void_p),
            ('vosk_model_free',[ctypes.c_void_p],None),
            ('vosk_model_find_word',[ctypes.c_void_p,ctypes.c_char_p],ctypes.c_int),
            ('vosk_recognizer_new',[ctypes.c_void_p,ctypes.c_float],ctypes.c_void_p),
            ('vosk_recognizer_new_grm',[ctypes.c_void_p,ctypes.c_float,ctypes.c_char_p],ctypes.c_void_p),
            ('vosk_recognizer_accept_waveform',[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_int],ctypes.c_int),
            ('vosk_recognizer_set_words',[ctypes.c_void_p,ctypes.c_int],None),
            ('vosk_recognizer_partial_result',[ctypes.c_void_p],ctypes.c_char_p),
            ('vosk_recognizer_result',[ctypes.c_void_p],ctypes.c_char_p),
            ('vosk_recognizer_final_result',[ctypes.c_void_p],ctypes.c_char_p),
            ('vosk_recognizer_free',[ctypes.c_void_p],None)):
            fn=getattr(self.lib,name);fn.argtypes=args;fn.restype=result
        self.lib.vosk_set_log_level(-1)
        self.model=self.lib.vosk_model_new(str(model).encode())
        try:
            if not self.model:raise ValueError('model_unavailable')
            if phrase is None:self.rec=self.lib.vosk_recognizer_new(self.model,16000)
            else:
                if phrase not in PHRASES.values() or any(self.lib.vosk_model_find_word(self.model,w.encode())<0 for w in phrase.split()):
                    raise ValueError('model_unavailable')
                self.rec=self.lib.vosk_recognizer_new_grm(self.model,16000,json.dumps([phrase,'[unk]']).encode())
            if not self.rec:raise ValueError('model_unavailable')
            self.lib.vosk_recognizer_set_words(self.rec,1)
        except Exception:self.close();raise

    def wake(self,pcm,phrase):
        final=self.lib.vosk_recognizer_accept_waveform(self.rec,pcm,len(pcm))
        raw=(self.lib.vosk_recognizer_result(self.rec) if final else self.lib.vosk_recognizer_partial_result(self.rec))
        doc=json.loads(raw)
        return (' '+phrase+' ') in (' '+(doc.get('text') or doc.get('partial') or '')+' ')

    def transcribe(self,pcm):
        wav_bytes(pcm)
        parts=[]
        for at in range(0,len(pcm),2560):
            chunk=pcm[at:at+2560]
            if self.lib.vosk_recognizer_accept_waveform(self.rec,chunk,len(chunk)):
                parts.append(json.loads(self.lib.vosk_recognizer_result(self.rec)))
        parts.append(json.loads(self.lib.vosk_recognizer_final_result(self.rec)))
        words=[word for part in parts for word in part.get('result',[])]
        if not words or sum(w.get('conf',0) for w in words)/len(words)<.65:
            raise ValueError('transcription_uncertain')
        return clean_text(' '.join(part.get('text','') for part in parts))

    def close(self):
        if self.rec:self.lib.vosk_recognizer_free(self.rec);self.rec=None
        if self.model:self.lib.vosk_model_free(self.model);self.model=None
        if self._dll is not None:self._dll.close();self._dll=None


def clean_text(text):
    if not isinstance(text,str) or not text.strip() or len(text)>400 or any(ord(c)<32 for c in text):
        raise ValueError('transcription_uncertain')
    return text.strip()


def whisper_transcribe(pcm,language,executable,model,process=run_process):
    if language not in ('en','de') or not executable or not model:raise ValueError('transcription_unconfigured')
    payload=wav_bytes(pcm)
    # Private PCM is passed through stdin and is never written into scratch.
    with temporary_directory(prefix='racecast-radio-stt-') as scratch:
        invocation=ai_agents.Invocation([executable,'-m',model,'-f','-','-of','result','-l',language,'-t','2','-np','-nt'],scratch,'',8,'speech')
        raw=process(invocation,payload,threading.Event(),8,ai_jobs.execution_env(os.environ),
                    output_limit=4096,input_limit=PCM_MAX+128,inherit_tree=True)
    return clean_text(raw.decode('utf8','replace').strip())


def google_transcribe(pcm,language,key,open_url=http_util.open_url):
    wav_bytes(pcm)
    if language not in ('en','de') or not isinstance(key,str) or not key or len(key)>256 or any(ord(c)<32 for c in key):
        raise ValueError('transcription_unconfigured')
    body={'config':{'encoding':'LINEAR16','sampleRateHertz':16000,'languageCode':'en-US' if language=='en' else 'de-DE'},
          'audio':{'content':base64.b64encode(pcm).decode()}}
    with open_url('https://speech.googleapis.com/v1/speech:recognize',data=json.dumps(body).encode(),method='POST',
                  headers={'Content-Type':'application/json','X-Goog-Api-Key':key},timeout=8,allow_redirects=False) as reply:
        raw=reply.read(65537)
    if len(raw)>65536:raise ValueError('transcription_failed')
    doc=json.loads(raw)
    parts=[row['alternatives'][0] for row in doc.get('results',[]) if row.get('alternatives')]
    if not parts or any(part.get('confidence',0)<.65 for part in parts):raise ValueError('transcription_uncertain')
    return clean_text(' '.join(part['transcript'] for part in parts))

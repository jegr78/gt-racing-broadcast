"""Private native capture worker. All microphone PCM is volatile and bounded."""
import json
import shutil
import subprocess
import sys
import time

import logsetup
import driver_radio_recognition as recognition
from driver_radio_voice_config import PHRASES


FIELDS={'version','platform','microphone','language','wake','stt','paths','whisper_exe','whisper_model','google_key'}


def validate(body):
    if (not isinstance(body,dict) or set(body)!=FIELDS or type(body['version']) is not int or body['version']!=1
            or body['language'] not in ('en','de') or body['wake'] not in PHRASES
            or body['stt'] not in ('vosk','whisper','google')
            or not isinstance(body['paths'],dict) or set(body['paths'])!={'library','en','de'}
            or any(not isinstance(v,str) or not v or len(v)>4096 or any(ord(c)<32 for c in v) for v in body['paths'].values())
            or any(not isinstance(body[k],str) or len(body[k])>4096 or any(ord(c)<32 for c in body[k])
                   for k in ('whisper_exe','whisper_model','google_key'))):
        raise ValueError('invalid_request')
    recognition.capture_args(body['platform'],body['microphone'])
    return body


def emit(event,**fields):
    sys.stdout.write(json.dumps(dict(event=event,**fields),ensure_ascii=True)+'\n');sys.stdout.flush()


def run(body):
    body=validate(body);proc=None;wake=None;transcriber=None
    try:
        paths=body['paths'];phrase=PHRASES[body['wake']]
        wake=recognition.Vosk(paths['library'],paths['en'],phrase=phrase)
        if body['stt']=='vosk':
            transcriber=recognition.Vosk(paths['library'],paths[body['language']])
        if body['stt']=='whisper' and (not body['whisper_exe'] or not body['whisper_model']):
            raise ValueError('transcription_unconfigured')
        if body['stt']=='google' and not body['google_key']:raise ValueError('transcription_unconfigured')
        exe=shutil.which('ffmpeg')
        if not exe:raise ValueError('microphone_unavailable')
        # Inherit the supervisor's owned group/Job Object, never create an escape.
        proc=subprocess.Popen([exe,*recognition.capture_args(body['platform'],body['microphone'])],
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        capture=recognition.Utterance();heartbeat=time.monotonic();emit('listening')
        while pcm:=proc.stdout.read(2560):
            if time.monotonic()-heartbeat>=1:emit('heartbeat');heartbeat=time.monotonic()
            utterance=capture.feed(pcm,False if capture.active else wake.wake(pcm,phrase))
            if utterance is None:continue
            if not utterance:raise ValueError('utterance_timeout')
            emit('transcribing')
            if body['stt']=='vosk':text=transcriber.transcribe(utterance)
            elif body['stt']=='whisper':text=recognition.whisper_transcribe(utterance,body['language'],body['whisper_exe'],body['whisper_model'])
            else:text=recognition.google_transcribe(utterance,body['language'],body['google_key'])
            emit('question',text=text);return
        raise ValueError('microphone_unavailable')
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=2)
            proc.stdout.close()
        if transcriber is not None:transcriber.close()
        if wake is not None:wake.close()


def main():
    logsetup.harden_stdio()
    try:
        raw=sys.stdin.buffer.readline(16385)
        if len(raw)>16384:raise ValueError('invalid_request')
        run(json.loads(raw))
    except Exception as exc:
        code=str(exc) if type(exc) is ValueError else 'transcription_failed'
        if code not in ('microphone_unavailable','model_unavailable','transcription_failed','transcription_uncertain',
                        'transcription_unconfigured','utterance_timeout','invalid_request'):
            code='native_unavailable'
        emit('error',code=code)
    return 0


if __name__=='__main__':raise SystemExit(main())

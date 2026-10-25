"""Relay-owned native voice lifecycle. Closing browser clients has no effect."""
import copy
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import driver_radio_access as access
import driver_radio_voice_config as config
from driver_radio_voice_process import Owned


class Supervisor:
    def __init__(self,service,machine,microphone=lambda:'',worker=None,player=None,
                 clock=time.monotonic,ready=None):
        self.service,self.machine,self.microphone=service,Path(machine),microphone
        self.worker_factory=worker or self._worker
        self.player_factory=player or self._player
        self.clock,self.ready=clock,ready or (lambda:config.ready(self.machine))
        self._lock=threading.RLock();self._stop=threading.Event();self._thread=None
        self._worker_proc=None;self._player_proc=None;self._ticket=None;self._signature=None
        self._binding=None;self._revision=0;self._paused=False;self._closed=False
        self._phase='disabled';self._error=None;self._retry_at=0;self._heartbeat=0;self._settle=0
        self._volume=.8;self._muted=False;self._language='en';self._wake='hey_jeff';self._enabled=False
        self._control_changed=False

    def _worker(self,body):
        executable=([sys.executable,'_radio-voice-worker'] if getattr(sys,'frozen',False) else
                    [sys.executable,str(Path(__file__).with_name('driver_radio_voice_worker.py'))])
        return Owned(executable,json.dumps(body).encode()+b'\n',config.execution_env(),events=True)

    def _player(self,audio,volume):
        exe=shutil.which('ffplay')
        if not exe:raise ValueError('player_unavailable')
        return Owned([exe,'-nodisp','-autoexit','-loglevel','error','-volume',str(round(volume*100)),
                      '-i','pipe:0'],audio,config.execution_env())

    def _set(self,phase,error=None):
        if (self._phase,self._error)!=(phase,error):
            self._phase,self._error=phase,error;self._revision+=1

    def status(self):
        with self._lock:
            return copy.deepcopy(dict(version=1,revision=self._revision,binding=self._binding,
                enabled=self._enabled and not self._closed,paused=self._paused,phase=self._phase,error=self._error,
                volume=self._volume,muted=self._muted,input_language=self._language,wake=self._wake,
                phrases=list(config.PHRASES) if self.ready() else [],topology='local_pc'))

    def control(self,body):
        with self._lock:
            if (not isinstance(body,dict) or set(body)-{'binding','expected','paused','volume','muted','input_language'}
                    or not {'binding','expected'}<=set(body)
                    or any(type(body[k]) is not bool for k in ('paused','muted') if k in body)
                    or 'volume' in body and (type(body['volume']) not in (int,float) or not 0<=body['volume']<=1)
                    or 'input_language' in body and body['input_language'] not in ('en','de')):
                raise access.RadioError('invalid_body','Invalid native voice controls')
            if type(body['expected']) is not int or body['expected']!=self._revision or body['binding']!=self._binding:
                raise access.RadioError('conflict','Native voice state changed; reload')
            for field,target in (('paused','_paused'),('volume','_volume'),('muted','_muted'),('input_language','_language')):
                if field in body:setattr(self,target,body[field])
            self._control_changed=True
            self._revision+=1
            return self.status()

    def _stop_capture(self):
        if self._worker_proc is not None:
            self._worker_proc.close();self._worker_proc=None

    def _stop_output(self,outcome):
        if self._player_proc is not None:
            self._player_proc.close();self._player_proc=None
        if self._ticket is not None:
            self.service.finish_playback(self._ticket.id,outcome);self._ticket=None
            self._settle=self.clock()+.3

    def step(self):
        with access._LOCK,self._lock:
            if self._closed:return
            settings=self.service.settings.read();state=self.service.refresh(force=True)
            voice=settings['voice'];native=config.settings(settings.get('native'))
            signature=(settings['revision'],state['binding'])
            enabled=settings['enabled'] and self.service.settings.available and voice['enabled']
            if signature!=self._signature:
                old_binding=self._binding
                self._stop_capture();self._stop_output('text_only')
                self._binding=copy.deepcopy(state['binding']);self._signature=copy.deepcopy(signature)
                self._volume,self._muted=native['volume'],native['muted']
                self._language,self._wake=voice['input_language'],native['wake']
                if self._binding!=old_binding:self._paused=False
                self._revision+=1
            self._enabled=enabled
            if self._control_changed:
                self._stop_capture();self._stop_output('text_only');self._control_changed=False
            if not enabled or self._paused:
                self._stop_capture();self._stop_output('text_only')
                self._set('disabled' if not enabled else 'paused');return
            if self._ticket is not None:
                # Force freshness and cancellation checks before every player poll.
                audio=self.service.audio_for(self._ticket.id)
                if self._ticket.cancelled.is_set() or self._muted:
                    self._stop_output('text_only');return
                if self._player_proc is not None:
                    rc=self._player_proc.poll()
                    if rc is not None:
                        self._stop_output('spoken' if rc==0 else 'failed')
                        if rc:self._set('error','playback_failed')
                    return
                if audio is not None:
                    try:self._player_proc=self.player_factory(audio,self._volume)
                    except (OSError,ValueError):
                        self._stop_output('failed');self._set('error','playback_unavailable')
                    return
                message=next((m for m in self.service.history()['messages'] if m['id']==self._ticket.message['id']),None)
                if message is None or message['audio_state'] in ('unconfigured','failed','not_requested'):
                    self._stop_output('text_only');return
                self._set('responding');return
            if self.service.pending_count():
                self._stop_capture()
                self._ticket=self.service.take_playback()
                if self._ticket is not None:
                    self._set('responding')
                    if self._muted:self._stop_output('text_only')
                    else:
                        audio=self.service.audio_for(self._ticket.id)
                        if audio is not None:
                            try:self._player_proc=self.player_factory(audio,self._volume)
                            except (OSError,ValueError):self._stop_output('failed');self._set('error','playback_unavailable')
                    return
            if self._worker_proc is not None:
                for event in self._worker_proc.read():
                    if event.get('event')=='question':
                        self._stop_capture()
                        try:self.service.question(event.get('text'),self._binding)
                        except access.RadioError:self._set('error','question_rejected')
                        else:self._set('responding')
                        return
                    if event.get('event') in ('heartbeat','listening','transcribing'):
                        self._heartbeat=self.clock()
                        if event['event']!='heartbeat':self._set(event['event'])
                    elif event.get('event')=='error':
                        self._stop_capture();self._retry_at=self.clock()+5
                        self._set('error',event.get('code') if event.get('code') in (
                            'microphone_unavailable','model_unavailable','transcription_failed','transcription_uncertain',
                            'transcription_unconfigured','utterance_timeout') else 'worker_failed');return
                if self._worker_proc.poll() is not None or self.clock()-self._heartbeat>20:
                    self._stop_capture();self._retry_at=self.clock()+5;self._set('error','worker_failed')
                return
            if self.clock()<max(self._retry_at,self._settle):return
            if not self.ready():self._set('error','models_unavailable');return
            mic=voice['microphone'] or self.microphone()
            if not mic:self._set('error','microphone_unavailable');return
            body=dict(version=1,platform=sys.platform,microphone=mic,language=self._language,wake=self._wake,
                      stt=native['stt'],paths=config.paths(self.machine),
                      whisper_exe=os.environ.get('RACECAST_RADIO_WHISPER_EXE') or shutil.which('whisper-cli') or '',
                      whisper_model=os.environ.get('RACECAST_RADIO_WHISPER_MODEL') or str(self.machine/'radio-voice'/'ggml-tiny.bin'),
                      google_key=os.environ.get('RACECAST_GCLOUD_STT_KEY','') if native['stt']=='google' else '')
            try:self._worker_proc=self.worker_factory(body)
            except (OSError,ValueError):
                self._retry_at=self.clock()+5;self._set('error','worker_failed');return
            self._heartbeat=self.clock();self._set('listening')

    def start(self):
        with self._lock:
            if self._thread is not None or self._closed:return
            def run():
                while not self._stop.is_set():
                    try:self.step()
                    except (OSError,ValueError,TypeError):
                        # Keep ownership references until a subsequent cleanup succeeds.
                        # Never acknowledge a playback slot after failed termination.
                        with self._lock:self._set('error','native_unavailable')
                        self._stop.wait(5)
                    self._stop.wait(.1)
            self._thread=threading.Thread(target=run,name='radio-native',daemon=True);self._thread.start()

    def close(self):
        self._stop.set()
        with self._lock:
            self._closed=True;self._stop_capture();self._stop_output('text_only');self._set('disabled')
        if self._thread is not None:self._thread.join(2)

#!/usr/bin/env python3
"""Bounded PCM, local wake gating and replaceable post-wake transcription."""
import io,json,sys,wave,struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_recognition as r


def t_capture_arguments_are_native_and_preserve_device_identity():
    for platform,fmt,spec in (('linux','pulse','source.id'),('win32','dshow','audio=source.id'),('darwin','avfoundation',':source.id')):
        args=r.capture_args(platform,'source.id')
        assert args[args.index('-f')+1]==fmt and args[args.index('-i')+1]==spec
        assert args[-2:]==['s16le','pipe:1'] and '16000' in args


def t_continuous_wake_and_question_uses_bounded_memory_and_no_ambient_transcription():
    state=r.Utterance();speech=struct.pack('<h',1000)*1280;silence=b'\0'*2560
    for _ in range(50):assert state.feed(speech,False) is None
    assert len(state.buffer)<=r.PRE_ROLL
    assert state.feed(speech,True) is None
    for _ in range(8):assert state.feed(speech,False) is None
    result=None
    for _ in range(12):
        result=state.feed(silence,False)
        if result is not None:break
    assert result and speech in result and len(result)<=r.PCM_MAX
    assert state.feed(silence,False) is None, 'one wake yields only one utterance'
    state=r.Utterance()
    for _ in range(300):state.feed(speech,True)
    assert len(state.buffer)<=r.PCM_MAX


def t_cloud_transcriber_only_receives_the_bounded_utterance_and_selected_language():
    calls=[]
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return json.dumps({'results':[{'alternatives':[{'transcript':'Wie viel Sprit habe ich?','confidence':.9}]}]}).encode()
    def request(url,**kwargs):calls.append((url,kwargs));return Reply()
    text=r.google_transcribe(b'\0'*3200,'de','private-key',open_url=request)
    assert text=='Wie viel Sprit habe ich?'
    data=json.loads(calls[0][1]['data']);assert data['config']['languageCode']=='de-DE'
    assert calls[0][1]['allow_redirects'] is False and 'private-key' not in calls[0][0]
    for key,pcm in (('',b'\0'*100),( 'key',b'x'*(r.PCM_MAX+1))):
        try:r.google_transcribe(pcm,'en',key,open_url=request)
        except ValueError:pass  # The unsafe input or failed cleanup must be rejected.
        else:raise AssertionError('Invalid or unconfigured STT accepted')
    assert len(calls)==1


def t_whisper_adapter_sends_wav_via_stdin_without_recording_a_file():
    seen=[]
    def process(invocation,prompt,stopped,budget,env,**kwargs):
        argv=invocation.argv;seen.append(argv)
        with wave.open(io.BytesIO(prompt),'rb') as f:assert f.getframerate()==16000 and f.getnchannels()==1
        assert argv[argv.index('-f')+1]=='-' and argv[argv.index('-l')+1]=='de'
        assert budget==8 and kwargs['output_limit']==4096 and kwargs['input_limit']<=1024*1024
        assert kwargs['inherit_tree'] is True, 'Whisper must stay inside the native owner tree'
        assert not list(Path(invocation.cwd).iterdir()), 'microphone audio must stay off disk'
        return b'Wie warm sind die Reifen?'
    assert 'Reifen' in r.whisper_transcribe(b'\0'*3200,'de','whisper-fixture','weights.bin',process=process)
    assert len(seen)==1


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

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


def t_an_armed_capture_listens_without_wake_and_gives_up_without_speech():
    speech=struct.pack('<h',1000)*1280;silence=b'\0'*2560
    # Interview answers (#1088): no wake word, but speech has to start inside the window.
    state=r.Utterance(listen_s=5)
    result=None
    for _ in range(int(5*32000/2560)+2):
        result=state.feed(silence,False)
        if result is not None:break
    assert result==b'',result
    state=r.Utterance(listen_s=5)
    for _ in range(10):assert state.feed(silence,False) is None
    for _ in range(8):assert state.feed(speech,False) is None
    for _ in range(12):
        result=state.feed(silence,False)
        if result is not None:break
    assert result and speech in result
    # Silence before the answer never ends the capture early.
    state=r.Utterance(listen_s=5)
    assert all(state.feed(silence,False) is None for _ in range(30))


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



class FakeVoskLib:
    """The libvosk calls Vosk makes, with one scripted final result."""
    def __init__(self,result,known=('race','two')):
        self.result,self.known,self.grammar=result,known,None
        for name in ('vosk_set_log_level','vosk_model_free','vosk_recognizer_set_words','vosk_recognizer_free',
                     'vosk_recognizer_partial_result','vosk_recognizer_result'):
            setattr(self,name,self._fn(lambda *a:None))
        self.vosk_model_new=self._fn(lambda path:1)
        self.vosk_model_find_word=self._fn(lambda model,word:0 if word.decode() in self.known else -1)
        self.vosk_recognizer_new=self._fn(lambda model,rate:2)
        self.vosk_recognizer_new_grm=self._fn(lambda model,rate,grammar:setattr(self,'grammar',json.loads(grammar)) or 3)
        self.vosk_recognizer_accept_waveform=self._fn(lambda rec,pcm,n:0)
        self.vosk_recognizer_final_result=self._fn(lambda rec:json.dumps(self.result).encode())
    @staticmethod
    def _fn(body):
        def fn(*args):return body(*args)
        return fn


def vosk(tmp,result,words=None):
    from unittest.mock import patch
    lib=FakeVoskLib(result)
    with patch.object(r.ctypes,'CDLL',lambda path:lib):
        return lib,r.Vosk(str(Path(tmp)/'libvosk'),'model',words=words)


def t_an_answer_grammar_keeps_only_words_the_model_knows():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lib,engine=vosk(d,{},words=['race','two','fünfundzwanzig'])
        assert lib.grammar==['race','two','[unk]'],lib.grammar
        engine.close()
        lib,engine=vosk(d,{})
        assert lib.grammar is None,'without words the free transcription runs'


def t_transcription_drops_unknown_words_and_reports_what_it_heard():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _,engine=vosk(d,{'text':'[unk] race','result':[{'word':'[unk]','conf':.2},{'word':'race','conf':.9}]},words=['race'])
        try:text=engine.transcribe(b'\0'*3200)
        except r.Uncertain as exc:raise AssertionError(f'[unk] must not count as heard speech: {exc.heard!r}') from None
        assert text=='race' and engine.confidence==.9,(text,engine.confidence)
        for result,heard in (({'text':'two','result':[{'word':'two','conf':.55}]},'two'),
                             ({'text':'[unk]','result':[{'word':'[unk]','conf':1}]},'')):
            _,engine=vosk(d,result,words=['two'])
            try:engine.transcribe(b'\0'*3200)
            except r.Uncertain as exc:
                assert str(exc)=='transcription_uncertain' and exc.heard==heard,(exc,exc.heard)
            else:raise AssertionError(result)


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

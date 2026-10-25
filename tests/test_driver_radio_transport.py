#!/usr/bin/env python3
"""Cloud speech transport is private IPC with a killable end-to-end deadline."""
import base64
import io
import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_providers as providers
from test_driver_radio_providers import wav,rejected


def t_production_transport_keeps_credentials_out_of_argv_and_environment():
    calls=[]
    def runner(invocation,prompt,stopped,budget,env,**kwargs):
        calls.append(invocation)
        payload=json.loads(prompt)
        assert payload['key']=='private-fixture-key' and payload['allow_cloud'] is True
        assert all('private-fixture-key' not in str(arg) for arg in invocation.argv)
        assert 'RACECAST_CONSOLE_SECRET' not in env and kwargs['output_limit']==providers.JSON_MAX
        assert invocation.output=='' and budget<=8
        return json.dumps({'audioContent':base64.b64encode(wav()).decode()}).encode()
    with patch.dict('os.environ',{'RACECAST_CONSOLE_SECRET':'private-console-secret'}):
        speech=providers.GoogleSpeech(key=lambda:'private-fixture-key',runner=runner)
        assert speech.synthesize('Fuel',threading.Event(),8)==wav()
    assert len(calls)==1 and not Path(calls[0].cwd).exists()


def t_frozen_transport_uses_the_private_entry_and_new_bootloader_environment():
    def runner(invocation,prompt,stopped,budget,env,**kwargs):
        assert invocation.argv==[sys.executable,'_radio-speech-worker']
        assert kwargs['extra_env']['PYINSTALLER_RESET_ENVIRONMENT']=='1'
        return json.dumps({'audioContent':base64.b64encode(wav()).decode()}).encode()
    with patch.object(sys,'frozen',True,create=True):
        assert providers.GoogleSpeech(key=lambda:'fixture-key',runner=runner).synthesize('Fuel',threading.Event(),8)==wav()


def t_private_worker_requires_a_complete_explicit_request_and_masks_failures():
    import driver_radio_tts_worker as worker
    seen=[]
    class Speech:
        def __init__(self,**kwargs):seen.append(kwargs['key']())
        def synthesize(self,text,stopped,budget):return wav()
    for body in ({}, {'key':'key','text':'Fuel','budget':8,'allow_cloud':False},
                 {'key':'key','text':'Fuel','budget':True,'allow_cloud':True}):
        out=io.StringIO();worker.main(stdin=io.BytesIO(json.dumps(body).encode()),stdout=out,speech_factory=Speech)
        assert json.loads(out.getvalue())['error']=='invalid_request'
    assert seen==[]
    out=io.StringIO();worker.main(stdin=io.BytesIO(json.dumps({'key':'fixture-key','text':'Fuel','budget':8,'allow_cloud':True}).encode()),stdout=out,speech_factory=Speech)
    assert base64.b64decode(json.loads(out.getvalue())['audioContent'])==wav() and seen==['fixture-key']
    class Failure(Speech):
        def synthesize(self,*args):raise OSError('private-key in a diagnostic')
    out=io.StringIO();worker.main(stdin=io.BytesIO(json.dumps({'key':'fixture-key','text':'Fuel','budget':8,'allow_cloud':True}).encode()),stdout=out,speech_factory=Failure)
    assert json.loads(out.getvalue())=={'error':'speech_failed'} and 'private-key' not in out.getvalue()


def t_blocked_transport_worker_cannot_hold_the_parent_indefinitely():
    import ai_agents
    import driver_radio_process
    def runner(invocation,prompt,stopped,budget,env,**kwargs):
        finished=Path(invocation.cwd)/'finished'
        script='import time,pathlib;time.sleep(2);pathlib.Path("finished").write_text("done")'
        blocked=ai_agents.Invocation([sys.executable,'-c',script],invocation.cwd,'',600,'speech')
        try:return driver_radio_process.run_process(blocked,prompt,stopped,budget,env,**kwargs)
        finally:assert not finished.exists(), 'transport deadline must stop the unfinished worker'
    rejected(lambda:providers.GoogleSpeech(key=lambda:'fixture-key',runner=runner).synthesize('Fuel',threading.Event(),.2),'timed_out')


def t_frozen_private_entry_skips_normal_profile_bootstrap():
    import driver_radio_tts_worker as worker
    import test_racecast
    m=test_racecast.m
    with patch.object(m,'_bootstrap',side_effect=AssertionError('Private IPC must not load profiles')),patch.object(worker,'main',return_value=0) as entry:
        assert m.main(['_radio-speech-worker'])==0
        entry.assert_called_once_with()


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

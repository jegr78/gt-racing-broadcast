#!/usr/bin/env python3
"""Isolated live CLI jobs use only synthetic fixtures and never invoke a provider."""
import json
import io
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import ai_agents
import ai_jobs
import driver_radio_ai as live
from driver_radio_providers import ProviderError
from test_driver_radio_service import Fixture


def rejected(fn,code):
    try:fn()
    except ProviderError as exc:assert exc.code==code,(exc.code,code)
    else:raise AssertionError('Expected '+code)


def config(mode='isolated'):
    return dict(id='live',name='Live radio',provider='codex',executable='fixture-cli',
                model='fixture-model',timeout=600,mode=mode,personal={})


class Adapter(ai_agents.Adapter):
    def __init__(self,settings):super().__init__(settings,which=lambda _:sys.executable)
    def probe(self,**kwargs):
        return dict(status='ready',auth='subscription',capabilities=list(ai_agents.REQUIRED_FLAGS['codex']),models=[])


def t_live_package_reuses_isolation_and_contains_only_allowed_facts():
    with tempfile.TemporaryDirectory() as d:
        machine=Path(d)/'machine';settings=ai_agents.Settings(machine)
        settings.save({'enabled':True,'agents':[config()]})
        f=Fixture(Path(d)/'profile');f.poll();message=f.request('pace')
        paths=[]
        def process(invocation,prompt,stopped,budget,env):
            paths.append(invocation.cwd)
            assert '--strict-config' in invocation.argv and '--ephemeral' in invocation.argv
            assert budget<=8 and 'RACECAST_CONSOLE_SECRET' not in env
            data=json.loads((Path(invocation.cwd)/'facts.json').read_text())
            assert 'facts' in data and str(machine) not in prompt.decode()
            assert 'private-test-secret' not in prompt.decode()
            result={'explanation':'The measured pace comparison is stable.','evidence':['pace.delta_s'],'claims':[]}
            Path(invocation.output).write_text(json.dumps(result),encoding='utf8')
            return b''
        with patch.dict(os.environ,{'RACECAST_CONSOLE_SECRET':'private-test-secret'}):
            result=live.LiveAI(machine,adapter_factory=Adapter,process=process).explain('live',message,threading.Event(),8)
        assert result['explanation'].startswith('The measured')
        assert paths and all(not Path(p).exists() for p in paths), 'temporary live inputs must be removed'


def t_disabled_personal_busy_and_changed_configuration_do_not_publish():
    with tempfile.TemporaryDirectory() as d:
        machine=Path(d)/'machine';settings=ai_agents.Settings(machine)
        f=Fixture(Path(d)/'profile');f.poll();message=f.request()
        calls=[]
        def process(invocation,*args):calls.append(1);return b''
        worker=live.LiveAI(machine,adapter_factory=Adapter,process=process)
        rejected(lambda:worker.explain('live',message,threading.Event(),8),'unconfigured')
        settings.save({'enabled':False,'agents':[config()]})
        rejected(lambda:worker.explain('live',message,threading.Event(),8),'unconfigured')
        assert not calls
        settings.save({'enabled':True,'agents':[config('personal')]})
        rejected(lambda:worker.explain('live',message,threading.Event(),8),'isolation_required')
        assert not calls
        settings.save({'enabled':True,'agents':[config()]})
        lease=ai_jobs.Lease(machine).acquire()
        try:rejected(lambda:worker.explain('live',message,threading.Event(),8),'busy')
        finally:lease.close()
        assert not calls
        def changes(invocation,*args):
            Path(invocation.output).write_text(json.dumps({'explanation':'Fuel range is measured.',
                'evidence':['fuel.usable_laps'],'claims':[]}),encoding='utf8')
            updated=config();updated['model']='changed'
            settings.save({'enabled':True,'agents':[updated]})
            return b''
        rejected(lambda:live.LiveAI(machine,adapter_factory=Adapter,process=changes).explain('live',message,threading.Event(),8),'cancelled')


def t_deadline_rounding_cannot_increase_the_process_budget():
    # Windows can return the same monotonic tick throughout a short preparation.
    # (8.002 + 8) - 8.002 rounds to 8.000000000000002 on every platform.
    with patch.object(live,'time',SimpleNamespace(monotonic=lambda:8.002)):
        t_live_package_reuses_isolation_and_contains_only_allowed_facts()


def invocation(d,script):
    return ai_agents.Invocation([sys.executable,'-c',script],str(d),str(Path(d)/'result.json'),600,'claude')


def t_owned_process_is_bounded_and_cancellable():
    with tempfile.TemporaryDirectory() as d:
        result=live.run_process(invocation(d,'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'),
                                b'bounded input',threading.Event(),3,{})
        assert result==b'bounded input'
        script='import sys,time;sys.stdout.buffer.write(b"x"*'+str(live.STREAM_MAX+20000)+');sys.stdout.flush();time.sleep(20)'
        rejected(lambda:live.run_process(invocation(d,script),b'',threading.Event(),3,{}),'output_too_large')
        stopped=threading.Event();stopped.set()
        # A broken cancellation guard must never pass a mock PID to real cleanup.
        with patch.object(live.subprocess,'Popen') as spawn, \
             patch.object(ai_jobs,'WindowsTree') as tree, \
             patch.object(ai_jobs,'terminate_tree') as cleanup:
            spawn.return_value.stdout=io.BytesIO()
            spawn.return_value.stderr=io.BytesIO()
            spawn.return_value.stdin=io.BytesIO()
            spawn.return_value.poll.return_value=0
            spawn.return_value.returncode=0
            rejected(lambda:live.run_process(invocation(d,'pass'),b'',stopped,3,{}),'cancelled')
            spawn.assert_not_called()
            tree.assert_not_called()
            cleanup.assert_not_called()
        stopped.clear();timer=threading.Timer(.1,stopped.set);timer.start()
        try:rejected(lambda:live.run_process(invocation(d,'import time;time.sleep(20)'),b'',stopped,3,{}),'cancelled')
        finally:timer.join()


def t_deadline_kills_the_owned_descendant_tree():
    with tempfile.TemporaryDirectory() as d:
        pidfile=Path(d)/'child.pid'
        script='import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);pathlib.Path('+repr(str(pidfile))+').write_text(str(p.pid));time.sleep(30)'
        rejected(lambda:live.run_process(invocation(d,script),b'',threading.Event(),2,{}),'timed_out')
        assert pidfile.exists(), 'fixture must establish a descendant before timeout'
        if os.name!='nt':
            state=subprocess.run(['ps','-p',pidfile.read_text(),'-o','stat='],capture_output=True,text=True,errors='replace',check=False).stdout.strip()
            assert not state or state.startswith('Z'), 'live provider descendant survived timeout: '+state


def t_inherited_transport_keeps_large_private_input_bounded():
    with tempfile.TemporaryDirectory() as d:
        data=b'x'*100000
        script='import sys;print(len(sys.stdin.buffer.read()))'
        result=live.run_process(invocation(d,script),data,threading.Event(),3,{},input_limit=200000,inherit_tree=True)
        assert result.strip()==b'100000'
        # Invalid limits cannot reach process creation even in a fault-injection run.
        with patch.object(live.subprocess,'Popen') as spawn,patch.object(ai_jobs,'WindowsTree'),patch.object(ai_jobs,'terminate_tree'):
            spawn.return_value.stdout=io.BytesIO();spawn.return_value.stderr=io.BytesIO();spawn.return_value.stdin=io.BytesIO()
            spawn.return_value.poll.return_value=0;spawn.return_value.returncode=0
            for limit in (False,0,1024*1024+1):
                rejected(lambda bound=limit:live.run_process(invocation(d,'pass'),data,threading.Event(),3,{},input_limit=bound),'invalid_transport')
            spawn.assert_not_called()
        rejected(lambda:live.run_process(invocation(d,script),data,threading.Event(),3,{}),'input_too_large')


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

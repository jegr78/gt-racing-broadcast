#!/usr/bin/env python3
"""AI execution with real controlled subprocesses and no provider credentials."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import ai_jobs as j
import ai_agents
import ai_package


def package():
    p={'selection':{'recording_id':'a'*32,'recording':'fixture.gt7rec','session':1,'laps':[2]},'template':'consistency','language':'en'}
    return dict(p,fingerprint=ai_package.fingerprint(p))


def error(fn, code):
    try: fn()
    except j.JobError as e: assert e.code==code,(e.code,code)
    else: raise AssertionError('expected '+code)


class FakeAdapter:
    def __init__(self, root, behavior='ok', timeout=10):
        self.config=ai_agents.agent_config(dict(id='fake',name='Fixture',provider='codex',model='exact',timeout=timeout))
        self.executable=sys.executable;self.provider='codex';self.calls=0
        self.script=Path(root)/'agent.py'
        self.script.write_text("""import json,sys,time,subprocess,os
from pathlib import Path
mode,out=sys.argv[1:]
sys.stdin.read()
if mode=='tree':
 child=subprocess.Popen([sys.executable,'-c',"import time;time.sleep(60)"])
 Path(out).with_name('child.pid').write_text(str(child.pid))
 print(json.dumps({'type':'turn.started'}),flush=True)
 time.sleep(60)
elif mode=='closed_tree':
 child=subprocess.Popen([sys.executable,'-c',"import time;time.sleep(60)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 Path(__file__).with_name('closed-child.pid').write_text(str(child.pid))
 Path(out).write_text('{\"ok\":true}')
 print(json.dumps({'type':'turn.completed'}),flush=True)
elif mode=='approval':
 print('interactive approval required',file=sys.stderr);sys.exit(2)
elif mode=='model':
 print('model unavailable',file=sys.stderr);sys.exit(2)
elif mode=='symlink':
 target=Path(__file__).with_name('private.json');target.write_text('{\"secret\":true}')
 try: Path(out).symlink_to(target)
 except OSError:
  if os.name!='nt': raise
  private=target.parent/'private-dir';private.mkdir(exist_ok=True)
  (private/'result.json').write_text('{\"secret\":true}')
  Path(out).parent.rmdir()
  subprocess.run(['cmd','/c','mklink','/J',str(Path(out).parent),str(private)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
elif mode=='large': print('x'*5000000,flush=True)
elif mode=='malformed': Path(out).write_text('not-json')
else:
 Path(out).write_text(json.dumps({'ok':True}))
 print(json.dumps({'type':'turn.completed'}),flush=True)
""",encoding='utf-8')
        self.behavior=behavior
    def probe(self,env=None):return dict(status='ready',auth='subscription',version='fixture',capabilities=[])
    def check_execution(self,probe,model=None):return model or self.config['model']
    def invocation(self,pkg,out,schema,probe,model=None):
        self.calls+=1
        self.scratch_output=Path(out)
        return ai_agents.Invocation([sys.executable,str(self.script),self.behavior,str(Path(out)/'result.json')],str(pkg),str(Path(out)/'result.json'),self.config['timeout'],'codex')
    def reported_model(self,raw):return None
    def extract(self,raw,events):return ai_agents.Adapter(dict(id='fake',name='Fake',provider='codex',model='exact')).extract(raw,events)


def prepared(root):
    pkg=Path(root)/'package';pkg.mkdir();(pkg/'summary.md').write_text('synthetic only')
    (pkg/'result-schema.json').write_text('{}');(pkg/'detail.json').write_text(json.dumps(package()))
    (pkg/'manifest.json').write_text('{}')
    return pkg


def wait_for(fn,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if fn():return
        time.sleep(.02)
    raise AssertionError('state did not arrive')


def t_success_handoff_and_frozen_profile():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);adapter=FakeAdapter(d)
        runner=j.Runner(Path(d)/'machine',Path(d)/'profileA', 'profile-a')
        run=runner.run(package(),pkg,adapter,model='exact',validate=lambda result,p:dict(validated=result))
        assert run['state']=='completed' and run['profile']=='profile-a'
        assert run['requested_model']=='exact' and run['actual_model'] is None
        assert adapter.calls==1 and (Path(run['directory'])/'package'/'detail.json').is_file()
        assert run['agent']['id']=='fake' and 'executable' not in run['agent']
        assert runner.status('other')['active'] is None
        assert Path(run['directory']).is_relative_to((Path(d)/'profileA').resolve())
        pending=runner.run(package(),pkg,adapter)
        assert pending['state']=='awaiting_validation' and pending['report'] is None


def t_failure_classification_and_no_retry():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);runner=j.Runner(Path(d)/'machine',Path(d)/'profile','profile')
        for mode,code in [('approval','approval_required'),('model','unavailable_model'),('malformed','invalid_output'),('large','output_limit'),('symlink','invalid_output')]:
            adapter=FakeAdapter(d,mode)
            result=runner.run(package(),pkg,adapter)
            assert result['state']=='failed' and result['error']['code']==code,(mode,result)
            assert adapter.calls==1 and result['report'] is None
            if mode=='symlink':assert not (Path(result['directory'])/'output'/'result.json').exists(), 'unselected data copied through output symlink'
            assert (Path(result['directory'])/'package'/'detail.json').is_file()
        result=runner.run(package(),pkg,FakeAdapter(d),validate=lambda _r,_p: (_ for _ in ()).throw(ValueError('invalid references')))
        assert result['state']=='failed' and result['error']['code']=='validation_failed'


def t_cancel_tree_and_machine_admission():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);machine=Path(d)/'machine';runner=j.Runner(machine,Path(d)/'profileA','a')
        result=[];adapter=FakeAdapter(d,'tree')
        th=threading.Thread(target=lambda:result.append(runner.run(package(),pkg,adapter)))
        th.start()
        wait_for(lambda:runner.status('a')['active'] and runner.status('a')['active']['state']=='running')
        active=runner.status('a')['active'];job=active['id']
        wait_for((adapter.scratch_output/'child.pid').exists)
        other=j.Runner(machine,Path(d)/'profileB','b')
        error(lambda:other.run(package(),pkg,FakeAdapter(d)),'busy')
        hidden=other.status('b')['active']
        assert hidden['foreign'] is True and 'directory' not in hidden and 'selection' not in hidden
        assert runner.cancel(job) is True
        th.join(5);assert not th.is_alive()
        assert result[0]['state']=='cancelled' and result[0]['report'] is None
        assert other.run(package(),pkg,FakeAdapter(d))['state']=='awaiting_validation'
        error(lambda:runner.cancel('../x'),'invalid_job')


def t_timeout_and_clean_environment():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);runner=j.Runner(Path(d)/'machine',Path(d)/'profile','p',timeout_scale=.03)
        result=runner.run(package(),pkg,FakeAdapter(d,'tree'))
        assert result['state']=='timed_out' and result['error']['code']=='timed_out'
        assert result['report'] is None
        env=j.execution_env(dict(os.environ,USER='fixture-user',RACECAST_CONSOLE_SECRET='private',OPENAI_API_KEY='private'))
        assert 'RACECAST_CONSOLE_SECRET' not in env and 'OPENAI_API_KEY' not in env
        assert 'PATH' in env and env.get('USER')=='fixture-user'


def t_cross_process_lease_and_prepared_snapshot_guard():
    with tempfile.TemporaryDirectory() as d:
        machine=Path(d)/'machine';held=j.Lease(machine).acquire()
        code="import sys;sys.path.insert(0,sys.argv[1]);import ai_jobs as j;j.Lease(sys.argv[2]).acquire()"
        r=subprocess.run([sys.executable,'-c',code,str(Path(j.__file__).parent),str(machine)],capture_output=True,text=True,errors='replace',timeout=5)
        assert r.returncode!=0 and 'already running' in r.stderr
        held.close()
        pkg=prepared(d);(pkg/'unrelated.txt').write_text('unselected-secret')
        runner=j.Runner(machine,Path(d)/'profile','p');adapter=FakeAdapter(d)
        result=runner.run(package(),pkg,adapter)
        assert result['state']=='failed' and result['error']['code']=='invalid_package'
        assert adapter.calls==0
        (pkg/'unrelated.txt').unlink();(pkg/'detail.json').write_text('{}')
        result=runner.run(package(),pkg,adapter)
        assert result['state']=='failed' and result['error']['code']=='invalid_package' and adapter.calls==0


def t_success_still_terminates_closed_stream_descendants():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);runner=j.Runner(Path(d)/'machine',Path(d)/'profile','p')
        result=runner.run(package(),pkg,FakeAdapter(d,'closed_tree'))
        assert result['state']=='awaiting_validation'
        pid=int((Path(d)/'closed-child.pid').read_text())
        def dead():
            if os.name=='nt':
                import ctypes as ct
                kernel=ct.WinDLL('kernel32',use_last_error=True)
                kernel.OpenProcess.argtypes=[ct.c_uint32,ct.c_int,ct.c_uint32];kernel.OpenProcess.restype=ct.c_void_p
                kernel.WaitForSingleObject.argtypes=[ct.c_void_p,ct.c_uint32]
                kernel.CloseHandle.argtypes=[ct.c_void_p]
                h=kernel.OpenProcess(0x00100000,False,pid)
                if not h:return True
                try:return kernel.WaitForSingleObject(h,0)==0
                finally:kernel.CloseHandle(h)
            p=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True,errors='replace')
            return not p.stdout.strip() or p.stdout.strip().startswith('Z')
        try:assert dead(), 'normal CLI exit left a descendant alive'
        finally:
            if not dead():
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(pid),'/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else:
                    import signal
                    os.kill(pid,signal.SIGKILL)


def t_interruption_retains_cancelled_artifact_and_manual_guidance():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);runner=j.Runner(Path(d)/'machine',Path(d)/'profile','p')
        adapter=FakeAdapter(d,'approval')
        result=runner.run(package(),pkg,adapter)
        guidance=json.loads((Path(result['directory'])/'manual-invocation.json').read_text())
        assert guidance['cwd']==str(Path(result['directory'])/'package')
        assert adapter.calls==1 and guidance['argv'][0]==sys.executable
        assert not any('racecast-ai-' in a for a in guidance['argv'])
        original=j.time.sleep
        def interrupt(_seconds):
            j.time.sleep=original
            raise KeyboardInterrupt()
        try:
            j.time.sleep=interrupt
            result=runner.run(package(),pkg,FakeAdapter(d,'tree'))
        finally:j.time.sleep=original
        assert result['state']=='cancelled' and result['finished_at'],result
        assert not runner.status('p')['busy']


def t_interruption_cleans_tree_before_temporary_directory_exit():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);runner=j.Runner(Path(d)/'machine',Path(d)/'profile','p')
        original_temp=j.tempfile.TemporaryDirectory;original_sleep=j.time.sleep;original_kill=j.terminate_tree
        exited=[];cleaned=[]
        class CheckedTemp(original_temp):
            def __exit__(self,*args):
                exited.append(bool(cleaned))
                return super().__exit__(*args)
        def kill(proc,tree=None):
            original_kill(proc,tree);cleaned.append(True)
        def interrupt(_seconds):
            j.time.sleep=original_sleep
            raise KeyboardInterrupt()
        try:
            j.tempfile.TemporaryDirectory=CheckedTemp;j.terminate_tree=kill;j.time.sleep=interrupt
            result=runner.run(package(),pkg,FakeAdapter(d,'tree'))
        finally:
            j.tempfile.TemporaryDirectory=original_temp;j.terminate_tree=original_kill;j.time.sleep=original_sleep
        assert exited==[True], 'temporary directory cleanup preceded process-tree cleanup'
        assert result['state']=='cancelled',result


def t_windows_tree_waits_for_descendants_before_releasing_handle():
    calls=[]
    class Kernel:
        def TerminateJobObject(self,handle,code):calls.append('terminate');return True
        def QueryInformationJobObject(self,handle,kind,pointer,size,returned):
            calls.append('query');pointer._obj.active=1 if calls.count('query')==1 else 0
            return True
        def CloseHandle(self,handle):calls.append('close')
    tree=object.__new__(j.WindowsTree);tree.handle=1;tree.kernel=Kernel()
    tree.close();tree.close()
    assert calls==['terminate','query','query','close'] and tree.handle is None
    class FailedKernel(Kernel):
        def QueryInformationJobObject(self,*args):return False
    tree=object.__new__(j.WindowsTree);tree.handle=1;tree.kernel=FailedKernel()
    error(tree.close,'cleanup_failed')
    assert tree.handle is None and calls[-1]=='close'


def t_persistence_failure_never_returns_a_completed_report():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);runner=j.Runner(Path(d)/'machine',Path(d)/'profile','p')
        original=runner._update
        def update(directory,state,phase,progress):
            original(directory,state,phase,progress)
            if phase=='completed':raise OSError('controlled persistence failure')
        runner._update=update
        result=runner.run(package(),pkg,FakeAdapter(d),validate=lambda result,_p:{'validated':result})
        assert result['state']=='failed' and result['report'] is None,result


def t_tree_cleanup_is_not_repeated_after_validation():
    with tempfile.TemporaryDirectory() as d:
        pkg=prepared(d);runner=j.Runner(Path(d)/'machine',Path(d)/'profile','p')
        original=j.terminate_tree;calls=[]
        def terminate(proc,tree=None):calls.append(proc.pid);return original(proc,tree)
        try:
            j.terminate_tree=terminate
            result=runner.run(package(),pkg,FakeAdapter(d),validate=lambda r,_p:{'validated':r})
        finally:j.terminate_tree=original
        assert result['state']=='completed' and len(calls)==1


if __name__=='__main__':
    for name,fn in sorted(globals().copy().items()):
        if name.startswith('t_'):fn();print('PASS',name)

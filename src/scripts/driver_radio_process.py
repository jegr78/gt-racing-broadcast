"""Bounded private provider processes, including DNS/socket work, with owned trees."""

import os
import contextlib
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import ai_jobs

STREAM_MAX = 65536
INPUT_MAX = 32768


class ProviderError(ValueError):
    def __init__(self,code,message='Radio provider is unavailable',cleanup=None):
        self.code=code
        self.cleanup=cleanup
        super().__init__(message)


class _Cleanup:
    """Retain uncertain ownership, never retry a potentially reused POSIX ID."""
    def __init__(self,proc,tree,threads):
        self.proc,self.tree,self.threads=proc,tree,threads
        self.resources=[]
    def retain(self,resource):
        if resource is not None:self.resources.append(resource)


_QUARANTINE_LOCK=threading.Lock()
_QUARANTINED=[]


def ensure_ready():
    with _QUARANTINE_LOCK:
        if _QUARANTINED:raise ProviderError('cleanup_failed',cleanup=_QUARANTINED[0])


def _failed_cleanup(proc,tree,threads):
    owner=_Cleanup(proc,tree,threads)
    with _QUARANTINE_LOCK:_QUARANTINED.append(owner)
    return ProviderError('cleanup_failed',cleanup=owner)


@contextlib.contextmanager
def temporary_directory(*args,**kwargs):
    ensure_ready()
    # Explicit cleanup below handles success; an uncertain live child must not
    # lose its directory to an interpreter-exit finalizer either.
    kwargs.setdefault('delete',False)
    directory=tempfile.TemporaryDirectory(*args,**kwargs)
    try:yield directory.name
    except ProviderError as error:
        if error.cleanup is not None:
            error.cleanup.retain(directory);directory=None
        raise
    finally:
        if directory is not None:directory.cleanup()


def _output_bound(output,limit):
    if not output:return
    total=0
    for count,path in enumerate(Path(output).parent.iterdir()):
        info=path.lstat()
        if count>=16 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ProviderError('invalid_output')
        total+=info.st_size
        if info.st_size>limit or total>2*limit:
            raise ProviderError('output_too_large')


def run_process(invocation,prompt,stopped,budget,env,output_limit=STREAM_MAX,extra_env=None,input_limit=INPUT_MAX,inherit_tree=False):
    """Own the tree before executing it; bound both pipes and generated files."""
    deadline=time.monotonic()+budget
    if stopped.is_set():raise ProviderError('cancelled')
    if budget<=0:raise ProviderError('timed_out')
    if type(output_limit) is not int or not 1<=output_limit<=3*1024*1024:
        raise ProviderError('invalid_transport')
    if type(inherit_tree) is not bool:raise ProviderError('invalid_transport')
    if type(input_limit) is not int or not 1<=input_limit<=1024*1024:raise ProviderError('invalid_transport')
    if prompt is not None and len(prompt)>input_limit:raise ProviderError('input_too_large')
    ensure_ready()
    tree=None;proc=None;readers=[];writer=None
    overflow=threading.Event();stream_error=threading.Event()
    output=[bytearray(),bytearray()]
    try:
        tree=None if inherit_tree else ai_jobs.WindowsTree()
        flags=({'creationflags':0x08000000} if os.name=='nt' else {}) if inherit_tree else (
            {'creationflags':0x08000204} if os.name=='nt' else {'start_new_session':True})
        safe_env=ai_jobs.execution_env(os.environ)
        safe_env.update(ai_jobs.execution_env(env))
        for name,value in (extra_env or {}).items():
            if name in ('SSL_CERT_FILE','SSL_CERT_DIR','PYINSTALLER_RESET_ENVIRONMENT') and isinstance(value,str):
                safe_env[name]=value
        proc=subprocess.Popen(invocation.argv,cwd=invocation.cwd,env=safe_env,
                              stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,**flags)
        if tree is not None:tree.attach(proc)
        def drain(stream,target):
            try:
                while chunk:=stream.read1(4096):
                    if len(target)+len(chunk)>output_limit:
                        overflow.set();return
                    target.extend(chunk)
            except OSError:
                stream_error.set()
        for stream,target in zip((proc.stdout,proc.stderr),output,strict=True):
            thread=threading.Thread(target=drain,args=(stream,target),daemon=True)
            readers.append(thread);thread.start()
        if prompt is not None:
            def write_input():
                try:
                    with proc.stdin as stream:stream.write(prompt)
                except OSError:
                    stream_error.set()
            writer=threading.Thread(target=write_input,daemon=True);writer.start()
        while True:
            if stopped.is_set():raise ProviderError('cancelled')
            if overflow.is_set():raise ProviderError('output_too_large')
            _output_bound(invocation.output,output_limit)
            if proc.poll() is not None:break
            if time.monotonic()>=deadline:raise ProviderError('timed_out')
            time.sleep(.02)
        if proc.returncode:raise ProviderError('agent_failed')
    except (OSError,ai_jobs.JobError) as exc:
        raise ProviderError('agent_failed') from exc
    finally:
        if proc is not None:
            try:
                if inherit_tree:
                    # The outer native worker owns this group/Job Object. Never
                    # start or signal a second group that can escape its owner.
                    if proc.poll() is None:proc.kill()
                    proc.wait(timeout=10)
                else:ai_jobs.terminate_tree(proc,tree)
            except (ai_jobs.JobError,OSError,subprocess.TimeoutExpired) as exc:
                raise _failed_cleanup(proc,tree,readers+([writer] if writer is not None else [])) from exc
        elif tree is not None:
            try:tree.close()
            except (ai_jobs.JobError,OSError) as exc:raise _failed_cleanup(proc,tree,[]) from exc
        for thread in readers:thread.join(1)
        if writer is not None:writer.join(1)
        if proc is not None:
            for stream in (proc.stdout,proc.stderr):
                if stream is not None and not any(t.is_alive() for t in readers):stream.close()
    if overflow.is_set():raise ProviderError('output_too_large')
    if stream_error.is_set() or any(t.is_alive() for t in readers) or writer is not None and writer.is_alive():
        raise _failed_cleanup(proc,tree,readers+([writer] if writer is not None else []))
    _output_bound(invocation.output,output_limit)
    return bytes(output[0])


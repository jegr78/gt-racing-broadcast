"""One machine-wide, explicitly admitted subscription CLI analysis process tree."""
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import ai_agents
import ai_package
import gt7_context
import gt7_recording

MAX_OUTPUT_BYTES = 4*1024*1024
TERMINAL = {'completed','awaiting_validation','failed','cancelled','timed_out'}
ENV_KEYS = {'PATH','HOME','USER','LOGNAME','USERNAME','USERPROFILE','HOMEDRIVE','HOMEPATH','SYSTEMROOT','WINDIR',
            'APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)',
            'TEMP','TMP','TMPDIR','LANG','LC_ALL','CODEX_HOME','CLAUDE_CONFIG_DIR',
            'SSL_CERT_FILE','SSL_CERT_DIR','NODE_EXTRA_CA_CERTS','HTTPS_PROXY','HTTP_PROXY','NO_PROXY'}


class JobError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)


def execution_env(original):
    """Preserve installation/login locations, never profile secrets or API overrides."""
    return {k:v for k,v in original.items() if k.upper() in ENV_KEYS}


def _now():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load(path):
    try:
        with open(path,encoding='utf-8') as f:return json.load(f)
    except (OSError,ValueError):return None


def _save(path,doc):gt7_context._atomic(str(path),doc)


class Lease:
    """Kernel releases admission on crashes; no PID reuse or stale-lock deletion."""
    def __init__(self,machine):
        self.path=Path(machine)/'ai-job.lock';self.fd=None
    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT,0o600)
        if os.fstat(fd).st_size==0:os.write(fd,b' ')
        try:
            if os.name=='nt':
                import msvcrt
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd);raise JobError('busy','An AI analysis is already running on this machine') from e
        self.fd=fd
        return self
    def close(self):
        if self.fd is not None:
            os.close(self.fd);self.fd=None


class WindowsTree:
    """Owned Windows Job Object kills descendants even after their parent exits."""
    def __init__(self):
        self.handle=None
        if os.name!='nt':return
        import ctypes as ct
        class Basic(ct.Structure):
            _fields_=[('process_time',ct.c_longlong),('job_time',ct.c_longlong),('flags',ct.c_uint32),
                      ('minimum',ct.c_size_t),('maximum',ct.c_size_t),('active_limit',ct.c_uint32),
                      ('affinity',ct.c_size_t),('priority',ct.c_uint32),('scheduling',ct.c_uint32)]
        class Extended(ct.Structure):
            _fields_=[('basic',Basic),('io',ct.c_ulonglong*6),('process_memory',ct.c_size_t),
                      ('job_memory',ct.c_size_t),('peak_process',ct.c_size_t),('peak_job',ct.c_size_t)]
        self.kernel=ct.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[ct.c_void_p,ct.c_wchar_p]
        self.kernel.CreateJobObjectW.restype=ct.c_void_p
        self.kernel.SetInformationJobObject.argtypes=[ct.c_void_p,ct.c_int,ct.c_void_p,ct.c_uint32]
        self.kernel.AssignProcessToJobObject.argtypes=[ct.c_void_p,ct.c_void_p]
        self.kernel.CloseHandle.argtypes=[ct.c_void_p]
        self.kernel.TerminateJobObject.argtypes=[ct.c_void_p,ct.c_uint32]
        self.kernel.QueryInformationJobObject.argtypes=[ct.c_void_p,ct.c_int,ct.c_void_p,ct.c_uint32,ct.c_void_p]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle,9,ct.byref(info),ct.sizeof(info)):
            self.close();raise JobError('isolation_unavailable','Windows process-tree ownership could not be established; use the saved package manually')
    def attach(self,proc):
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle,int(proc._handle)):
            proc.kill();proc.wait(timeout=10);self.close()
            raise JobError('isolation_unavailable','Windows refused the owned process Job Object; no permission bypass was used')
        if self.handle:self.resume(proc)
    def resume(self,proc):
        import ctypes as ct
        class Entry(ct.Structure):
            _fields_=[('size',ct.c_uint32),('usage',ct.c_uint32),('thread',ct.c_uint32),('owner',ct.c_uint32),
                      ('base',ct.c_int32),('delta',ct.c_int32),('flags',ct.c_uint32)]
        self.kernel.CreateToolhelp32Snapshot.argtypes=[ct.c_uint32,ct.c_uint32]
        self.kernel.CreateToolhelp32Snapshot.restype=ct.c_void_p
        self.kernel.Thread32First.argtypes=[ct.c_void_p,ct.c_void_p]
        self.kernel.Thread32Next.argtypes=[ct.c_void_p,ct.c_void_p]
        self.kernel.OpenThread.argtypes=[ct.c_uint32,ct.c_int,ct.c_uint32]
        self.kernel.OpenThread.restype=ct.c_void_p
        self.kernel.GetProcessIdOfThread.argtypes=[ct.c_void_p]
        self.kernel.GetProcessIdOfThread.restype=ct.c_uint32
        self.kernel.ResumeThread.argtypes=[ct.c_void_p]
        self.kernel.ResumeThread.restype=ct.c_uint32
        snapshot=self.kernel.CreateToolhelp32Snapshot(4,0)
        if not snapshot or snapshot==ct.c_void_p(-1).value:
            raise JobError('isolation_unavailable','Could not identify the suspended analysis thread')
        resumed=False
        try:
            entry=Entry();entry.size=ct.sizeof(entry)
            available=self.kernel.Thread32First(snapshot,ct.byref(entry))
            while available:
                if entry.owner==proc.pid:
                    thread=self.kernel.OpenThread(0x0802,False,entry.thread)
                    if thread:
                        try:
                            if self.kernel.GetProcessIdOfThread(thread)==proc.pid and self.kernel.ResumeThread(thread)==1:
                                resumed=True;break
                        finally:self.kernel.CloseHandle(thread)
                available=self.kernel.Thread32Next(snapshot,ct.byref(entry))
        finally:self.kernel.CloseHandle(snapshot)
        if not resumed:raise JobError('isolation_unavailable','Could not resume the owned analysis process')
    def close(self):
        if not self.handle:return
        import ctypes as ct
        class Accounting(ct.Structure):
            _fields_=[('times',ct.c_longlong*4),('faults',ct.c_uint32),('total',ct.c_uint32),
                      ('active',ct.c_uint32),('terminated',ct.c_uint32)]
        handle=self.handle;self.handle=None
        try:
            if not self.kernel.TerminateJobObject(handle,1):
                raise JobError('cleanup_failed','Windows could not terminate the owned analysis Job Object')
            deadline=time.monotonic()+10
            while True:
                info=Accounting()
                if not self.kernel.QueryInformationJobObject(handle,1,ct.byref(info),ct.sizeof(info),None):
                    raise JobError('cleanup_failed','Windows could not confirm analysis descendant termination')
                if info.active==0:break
                if time.monotonic()>=deadline:raise JobError('cleanup_failed','Windows analysis descendants did not terminate')
                time.sleep(.01)
        finally:self.kernel.CloseHandle(handle)


def terminate_tree(proc,tree=None):
    """Terminate only the owned process group/Job Object, never a saved PID."""
    if os.name=='nt':
        if tree:tree.close()
        elif proc.poll() is None:proc.kill()
    else:
        proc.poll()  # Reap an exited leader before signalling its remaining group (Darwin zombie-only groups reject signals).
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass  # The owned group already exited.
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:raise JobError('cleanup_failed','The analysis process tree did not terminate') from None


def snapshot_package(prepared,destination,frozen):
    prepared=Path(prepared)
    names={'detail.json','summary.md','manifest.json','result-schema.json'}
    if prepared.is_symlink() or not prepared.is_dir() or {p.name for p in prepared.iterdir()}!=names:
        raise JobError('invalid_package','Prepared package contains missing or unrelated files')
    files={}
    for name in names:
        p=prepared/name
        if p.is_symlink() or not p.is_file() or p.stat().st_size>2*1024*1024:
            raise JobError('invalid_package','Prepared package files must be bounded regular files')
        files[name]=p.read_bytes()
    try:detail=json.loads(files['detail.json'])
    except (ValueError,UnicodeDecodeError) as e:raise JobError('invalid_package','Prepared package is not readable JSON') from e
    expected=copy.deepcopy(frozen);digest=expected.pop('fingerprint',None)
    if detail!=frozen or ai_package.fingerprint(expected)!=digest:
        raise JobError('invalid_package','Prepared input differs from the confirmed package snapshot')
    destination=Path(destination);destination.mkdir()
    for name,raw in files.items():(destination/name).write_bytes(raw)



def regular_output_bytes(path):
    path=Path(path)
    if path.is_symlink():raise JobError('invalid_output','Provider result cannot be a symlink')
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    except OSError as e:raise JobError('invalid_output','Provider omitted a readable regular result file') from e
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>MAX_OUTPUT_BYTES:
            raise JobError('invalid_output','Provider result must be a bounded unlinked regular file')
        raw=f.read(MAX_OUTPUT_BYTES+1)
        if len(raw)>MAX_OUTPUT_BYTES:raise JobError('invalid_output','Provider result exceeds the output bound')
        return raw


def retain_output(out,destination):
    """Never follow model-created links or copy unrelated output files."""
    out=Path(out);destination=Path(destination)
    if out.resolve()!=out.absolute():
        raise JobError('invalid_output','Provider redirected the dedicated output directory')
    destination.mkdir()
    result=out/'result.json'
    if result.exists() or result.is_symlink():
        (destination/'result.json').write_bytes(regular_output_bytes(result))


def save_manual_invocation(directory,invocation,package,output):
    """Retain the same restricted argv for a deliberate interactive/manual retry."""
    directory=Path(directory);saved_package=directory/'package';saved_output=directory/'manual-output'
    replacements=((str(package),str(saved_package)),(str(output),str(saved_output)))
    argv=[]
    for argument in invocation.argv:
        for old,new in replacements:
            argument=argument.replace(json.dumps(old)[1:-1],json.dumps(new)[1:-1]).replace(old,new)
        argv.append(argument)
    _save(directory/'manual-invocation.json',dict(argv=argv,cwd=str(saved_package),
        output_directory=str(saved_output),stdin=str(saved_package/'summary.md'),
        environment_allowlist=sorted(ENV_KEYS),
        instructions='Create output_directory, use only the listed environment keys, run argv from cwd with stdin redirected from the summary. Keep all sandbox/restricted flags. If interactive approval remains necessary, inspect permissions in the provider CLI externally; do not bypass them. A manual retry consumes additional subscription quota and transmits the same package to the provider.'))


def _diagnostic_code(text):
    lower=text.lower()
    if any(s in lower for s in ('approval required','approval_required','permission denied','permission_denied','permission prompt','interactive approval')):
        return 'approval_required','Interactive approval is required. Export the saved package and run the provider manually with scoped permissions.'
    if 'model' in lower and any(s in lower for s in ('unavailable','not available','not found','not supported')):
        return 'unavailable_model','The selected model is unavailable. Choose a model explicitly; no fallback was used.'
    if any(s in lower for s in ('rate limit','quota','usage limit')):
        return 'provider_limit','The provider rejected this run because of a limit. Retry deliberately when available.'
    if any(s in lower for s in ('not logged in','authentication failed','login required','failed to refresh oauth token')):
        return 'logged_out','Log in with the provider subscription CLI externally.'
    return 'provider_failed','The provider invocation failed; inspect saved local diagnostics.'


class Runner:
    def __init__(self,machine_runtime,profile_runtime,profile,timeout_scale=1):
        self.machine=Path(machine_runtime).resolve();self.profile_root=Path(profile_runtime).resolve()
        self.profile=profile;self.timeout_scale=timeout_scale
        self.active_path=self.machine/'ai-job.json'
        self.cancel_path=self.machine/'ai-job-cancel.json'

    def status(self,profile):
        active=_load(self.active_path)
        if active is None:
            return {'active':None,'busy':False}  # Admission remains kernel-guarded before announcement.
        lease=Lease(self.machine)
        try:lease.acquire()
        except JobError:
            active=_load(self.active_path) or {'state':'queued'}
            if active.get('profile')!=profile:
                return {'active':{'id':active.get('id'),'state':active.get('state','running'),'foreign':True},'busy':True}
            return {'active':active,'busy':True}
        else:
            lease.close();return {'active':None,'busy':False}

    def cancel(self,job_id):
        if not isinstance(job_id,str) or not re.fullmatch('[0-9a-f]{32}',job_id):
            raise JobError('invalid_job','Invalid analysis job ID')
        active=_load(self.active_path)
        if not active or active.get('id')!=job_id or not self.status(self.profile)['busy']:
            return False
        _save(self.cancel_path,{'id':job_id})
        return True

    def _claim(self,package,adapter,model):
        selection=copy.deepcopy(package.get('selection',{}))
        ident=selection.get('recording_id')
        if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}|[0-9a-f]{64}',ident):
            raise JobError('invalid_package','Invalid recording identity in package')
        cfg=copy.deepcopy(adapter.config)
        chosen=cfg['model'] if model is None else model
        if not isinstance(chosen,str) or not chosen.strip():
            raise JobError('unavailable_model','Select an explicit model before starting')
        lease=Lease(self.machine).acquire()
        job_id=uuid.uuid4().hex
        directory=self.profile_root/'telemetry-analyses'/ident/job_id
        try:
            if 'source' in package:
                sources=[(selection.get('recording'),ident,package['source'].get('sha256'))]
                for ref in package.get('references',[]):
                    provenance=next((p for p in package['reference_provenance'] if p['recording_id']==ref['recording_id']),{})
                    sources.append((ref['rec']+'.gt7rec',ref['recording_id'],provenance.get('sha256')))
                for name,source_id,digest in dict.fromkeys(sources):
                    if not isinstance(name,str) or Path(name).name!=name or '/' in name or '\\' in name or not name.endswith('.gt7rec'):
                        raise JobError('invalid_package','Invalid profile recording name')
                    path=self.profile_root/'telemetry-recordings'/name
                    try:
                        current=(path.is_file() and not path.is_symlink() and path.resolve().parent==(self.profile_root/'telemetry-recordings').resolve()
                                 and gt7_context.source_identity(str(path))==source_id and ai_package.file_hash(path)==digest)
                    except (OSError,ValueError,KeyError,TypeError,gt7_recording.RecordingError):current=False
                    if not current:raise JobError('stale_source','Selected recording/reference changed or was deleted after preview; prepare a new selection')
            if not directory.resolve().is_relative_to(self.profile_root):
                raise JobError('invalid_package','Analysis artifact directory leaves the profile')
            directory.mkdir(parents=True)
            state=dict(format='racecast-ai-run',version=1,id=job_id,profile=self.profile,
                       selection=selection,package_fingerprint=package['fingerprint'],
                       agent={k:v for k,v in cfg.items() if k!='executable'},
                       agent_fingerprint=None,
                       requested_model=chosen,actual_model=None,provider_version=None,
                       created_at=_now(),started_at=None,finished_at=None,state='queued',
                       progress='Preparing isolated analysis package',error=None,report=None,
                       quota_notice='Cancellation cannot recover already consumed subscription quota.')
            # Hash includes executable identity without exporting the machine path.
            state['agent_fingerprint']=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()
            _save(directory/'run.json',state)
            active=dict(state,directory=str(directory));_save(self.active_path,active)
            return lease,directory,state
        except BaseException:
            lease.close();raise

    def _update(self,directory,state,phase,progress):
        state.update(state=phase,progress=progress)
        _save(directory/'run.json',state)
        _save(self.active_path,dict(state,directory=str(directory)))

    def run(self,package,prepared_package,adapter,model=None,validate=None,claimed=None):
        frozen=copy.deepcopy(package)
        lease,directory,state=claimed or self._claim(frozen,adapter,model)
        proc=None;tree=None;clean_attempted=False;readers=[];overflow=threading.Event();events=[];reader_errors=[]
        def cleanup_tree():
            nonlocal clean_attempted
            if proc is not None and not clean_attempted:
                clean_attempted=True  # Never retry a possibly reused process-group identity.
                terminate_tree(proc,tree)
        try:
            if Path(prepared_package).resolve()!=(directory/'package').resolve():
                snapshot_package(prepared_package,directory/'package',frozen)
            state['input_file_fingerprints']={name:hashlib.sha256((directory/'package'/name).read_bytes()).hexdigest()
                                                for name in ('detail.json','summary.md','manifest.json','result-schema.json')}
            self._update(directory,state,'starting','Checking installed CLI and subscription authentication')
            probe=adapter.probe(env=dict(os.environ))
            state['provider_version']=probe.get('version')
            if (_load(self.cancel_path) or {}).get('id')==state['id']:
                raise JobError('cancelled','Analysis cancelled before provider invocation')
            if probe.get('status')!='ready':
                raise JobError(probe.get('status','auth_unknown'),probe.get('guidance','Check provider login externally'))
            with tempfile.TemporaryDirectory(prefix='racecast-ai-') as scratch:
                try:
                    pkg=Path(scratch).resolve()/'package';out=pkg.parent/'output'
                    shutil.copytree(directory/'package',pkg);out.mkdir()
                    if any(p.is_symlink() for p in pkg.rglob('*')):
                        raise JobError('invalid_package','Analysis packages cannot contain symlinks')
                    invocation=adapter.invocation(pkg,out,pkg/'result-schema.json',probe,model=state['requested_model'])
                    save_manual_invocation(directory,invocation,pkg,out)
                    if (_load(self.cancel_path) or {}).get('id')==state['id']:
                        raise JobError('cancelled','Analysis cancelled before provider invocation')
                    flags={'creationflags':0x08000204} if os.name=='nt' else {'start_new_session':True}
                    tree=WindowsTree()
                    proc=subprocess.Popen(invocation.argv,cwd=invocation.cwd,env=execution_env(os.environ),
                                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**flags)
                    tree.attach(proc)
                    state['started_at']=_now()
                    self._update(directory,state,'running','Provider invocation started')
                    def drain_impl(stream,target,parse=False):
                        count=0;pending=b''
                        with open(target,'wb') as f:
                            while True:
                                chunk=stream.read1(8192)
                                if not chunk:break
                                count+=len(chunk)
                                if count>MAX_OUTPUT_BYTES:overflow.set();break
                                f.write(chunk)
                                if parse:
                                    pending+=chunk
                                    lines=pending.split(b'\n');pending=lines.pop()
                                    if len(pending)>MAX_OUTPUT_BYTES:overflow.set();break
                                    for line in lines:
                                        try:event=json.loads(line.decode('utf-8','replace'))
                                        except ValueError:continue  # Provider diagnostics can be plain text.
                                        if isinstance(event,dict):events.append(event)
                        stream.close()
                    def drain(stream,target,parse=False):
                        try:drain_impl(stream,target,parse)
                        except Exception as exc:
                            reader_errors.append(type(exc).__name__);overflow.set()
                        finally:stream.close()
                    stdout=directory/'stdout.log';stderr=directory/'stderr.log'
                    for stream,target,parse in ((proc.stdout,stdout,True),(proc.stderr,stderr,False)):
                        reader=threading.Thread(target=drain,args=(stream,target,parse),daemon=True)
                        readers.append(reader);reader.start()
                    summary=(pkg/'summary.md').read_bytes()
                    def send_prompt():
                        try:proc.stdin.write(summary)
                        except BrokenPipeError:pass  # Classify early CLI rejection from its diagnostics.
                        finally:
                            try:proc.stdin.close()
                            except BrokenPipeError:pass  # The CLI can exit before reading the prompt.
                    writer=threading.Thread(target=send_prompt,daemon=True)
                    readers.append(writer);writer.start()
                    deadline=time.monotonic()+invocation.timeout*self.timeout_scale
                    reason=None
                    while True:
                        if (_load(self.cancel_path) or {}).get('id')==state['id']:
                            reason=JobError('cancelled','Analysis cancelled; consumed subscription quota is not recoverable');break
                        if overflow.is_set():reason=JobError('output_limit','Provider diagnostics exceeded the output bound');break
                        if time.monotonic()>=deadline:reason=JobError('timed_out','Analysis exceeded the configured timeout');break
                        if proc.poll() is not None:break
                        time.sleep(.05)
                    if reason:cleanup_tree()
                    else:proc.wait()
                    for reader in readers:reader.join(2)
                    if any(reader.is_alive() for reader in readers):
                        cleanup_tree()
                        raise JobError('cleanup_failed','A provider descendant retained the output pipe')
                    cleanup_tree()
                    retain_output(out,directory/'output')
                    if reason:raise reason
                    if reader_errors:raise JobError('execution_failed','Could not retain provider diagnostics')
                    if overflow.is_set():raise JobError('output_limit','Provider diagnostics exceeded the output bound')
                    diagnostics=stderr.read_text(encoding='utf-8',errors='replace')
                    if adapter.provider=='claude':
                        state['actual_model']=adapter.reported_model(stdout.read_text(encoding='utf-8',errors='replace'))
                    if proc.returncode:
                        code,message=_diagnostic_code(diagnostics+stdout.read_text(encoding='utf-8',errors='replace'))
                        raise JobError(code,message)
                    result_path=Path(invocation.output)
                    if adapter.provider=='codex':
                        if not result_path.is_file() or result_path.is_symlink() or result_path.stat().st_size>MAX_OUTPUT_BYTES:
                            raise JobError('invalid_output','Provider omitted a bounded regular result file')
                        raw=regular_output_bytes(result_path).decode('utf-8','replace')
                    else:raw=stdout.read_text(encoding='utf-8',errors='replace')
                    state['actual_model']=adapter.reported_model(raw)
                    try:extracted=adapter.extract(raw,events)
                    except ai_agents.AgentError as e:
                        if e.code=='provider_failed':
                            code,message=_diagnostic_code(diagnostics+stdout.read_text(encoding='utf-8',errors='replace'))
                            raise JobError(code,message) from e
                        raise
                    state['actual_model']=extracted['actual_model']
                    _save(directory/'structured-output.json',extracted['result'])
                    if validate is None:
                        self._update(directory,state,'awaiting_validation','Output saved; report validation is pending')
                    else:
                        try:
                            report=validate(extracted['result'],frozen)
                            if not isinstance(report,dict):raise ValueError('validator did not return a report')
                        except Exception as e:
                            (directory/'validation-error.log').write_text(type(e).__name__+': '+str(e),encoding='utf-8')
                            raise JobError('validation_failed','Structured output did not validate; inspect saved local diagnostics') from e
                        state['report']=report
                        self._update(directory,state,'completed','Validated analysis completed')
                finally:
                    cleanup_tree()  # Windows cannot remove a live process's working directory.
                    if tree:tree.close()
                    for reader in readers:reader.join(2)
        except KeyboardInterrupt:
            state['report']=None
            state['error']={'code':'cancelled','message':'Analysis interrupted; consumed subscription quota is not recoverable'}
            self._update(directory,state,'cancelled',state['error']['message'])
        except (JobError,ai_agents.AgentError) as e:
            state['report']=None
            state['error']={'code':e.code,'message':str(e)}
            phase=e.code if e.code in ('cancelled','timed_out') else 'failed'
            self._update(directory,state,phase,str(e))
        except Exception as e:
            state['report']=None
            (directory/'runner-error.log').write_text(type(e).__name__+': '+str(e),encoding='utf-8')
            state['error']={'code':'execution_failed','message':'Analysis execution failed; inspect saved local diagnostics'}
            self._update(directory,state,'failed',state['error']['message'])
        finally:
            try:
                cleanup_tree()
                if tree:tree.close()
                for reader in readers:reader.join(2)
                state['finished_at']=_now();_save(directory/'run.json',state)
                active=_load(self.active_path)
                if active and active.get('id')==state['id']:
                    self.active_path.unlink(missing_ok=True)
            finally:
                if tree:tree.close()
                lease.close()

        return dict(state,directory=str(directory))

    def start(self,package,prepared_package,adapter,model=None,validate=None):
        frozen=copy.deepcopy(package)
        claimed=self._claim(frozen,adapter,model)
        try:snapshot_package(prepared_package,claimed[1]/'package',frozen)
        except BaseException:
            self.active_path.unlink(missing_ok=True);claimed[0].close();raise
        prepared_package=claimed[1]/'package'
        worker=threading.Thread(target=self.run,args=(copy.deepcopy(package),prepared_package,adapter),
                                kwargs={'model':model,'validate':validate,'claimed':claimed},daemon=True)
        try:worker.start()
        except BaseException:
            claimed[0].close();raise
        return claimed[2]['id']

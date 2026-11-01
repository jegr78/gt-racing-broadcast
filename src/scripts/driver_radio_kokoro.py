"""Optional local Kokoro speech: explicit provisioning and one owned long-lived worker.

The runtime holds GPL packages and never enters the binary. The relay reaches it only
through a child process. Design: docs/superpowers/specs/2026-11-01-local-kokoro-speech-design.md
"""
import base64
import collections
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import ai_jobs
import driver_radio_models as models
from driver_radio_process import ProviderError,temporary_directory
from driver_radio_providers import CACHE_ENTRIES,CACHE_MAX,JSON_MAX,validate_wav
from driver_radio_voice_config import execution_env

THREADS = 4
RETRY_S = 5
CHECK_TIMEOUT_S = 180
INSTALL_TIMEOUT_S = 1200
ARCHIVE_MAX = 128*1024*1024
_PYTHON = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16+20261026-'
RUNTIMES = {
    'windows':(_PYTHON+'x86_64-pc-windows-msvc-install_only_stripped.tar.gz','1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149'),
    'macos_arm64':(_PYTHON+'aarch64-apple-darwin-install_only_stripped.tar.gz','353d5f1b4fd2715e7053f12f068435d5bbad16b91e057006b5d01e0925709a7e'),
    'linux_x64':(_PYTHON+'x86_64-unknown-linux-gnu-install_only_stripped.tar.gz','ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20'),
    'linux_arm64':(_PYTHON+'aarch64-unknown-linux-gnu-install_only_stripped.tar.gz','bb4b6392670c5d3f93876996fcaa19858a4a413a0c7720144c18b0d6f1f0db2d')}
_MODELS = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'
MODEL_FILES = {
    'kokoro-v1.0.int8.onnx':(_MODELS+'kokoro-v1.0.int8.onnx','6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb'),
    'voices-v1.0.bin':(_MODELS+'voices-v1.0.bin','bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d')}
MANIFEST = 'kokoro-ready.json'
MANIFEST_DOC = dict(version=1,python=RUNTIMES['windows'][0].split('/')[-2])


def target(system=None,machine=None):
    system=system or platform.system();machine=(machine or platform.machine()).lower()
    if system=='Windows' and machine in ('amd64','x86_64'):return 'windows'
    if system=='Darwin' and machine in ('arm64','aarch64'):return 'macos_arm64'
    if system=='Linux' and machine in ('x86_64','amd64'):return 'linux_x64'
    if system=='Linux' and machine in ('aarch64','arm64'):return 'linux_arm64'
    raise ValueError('Local Kokoro speech does not support this OS/CPU')


def _source():
    if getattr(sys,'frozen',False):return Path(sys._MEIPASS)/'src'
    return Path(__file__).resolve().parents[1]


def worker_script():
    return _source()/'scripts'/'driver_radio_kokoro_worker.py'


def requirements():
    return _source()/'assets'/'driver-radio'/'kokoro-requirements.txt'


def python_path(root,windows=None):
    windows=os.name=='nt' if windows is None else windows
    return Path(root)/'python'/('python.exe' if windows else 'bin/python3')


def ready(machine):
    root=Path(machine)/'radio-kokoro'
    try:
        if root.is_symlink():return False
        with (root/MANIFEST).open('rb') as stream:raw=stream.read(1025)
        return (len(raw)<=1024 and json.loads(raw)==MANIFEST_DOC and python_path(root).is_file()
                and all((root/'models'/name).is_file() for name in MODEL_FILES))
    except (OSError,ValueError,RecursionError):return False


def _run(argv,timeout):
    return subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,
                          env=execution_env(),**({'creationflags':0x08000000} if os.name=='nt' else {}))


def _check(result,what):
    if result.returncode!=0:
        lines=(result.stderr or result.stdout or '').strip().splitlines()
        raise ValueError(what+' failed'+(': '+lines[-1][:200] if lines else ''))


def provision(machine,fetch=models.download,run=_run,platform_key=None):
    machine=Path(machine);machine.mkdir(parents=True,exist_ok=True)
    dest=machine/'radio-kokoro'
    if dest.is_symlink():raise ValueError('Kokoro directory must not be a link')
    if ready(machine):return dict(version=1,kokoro='ready')
    runtime=RUNTIMES[platform_key or target()]
    if dest.exists():raise ValueError('Incomplete Kokoro runtime exists; remove runtime/radio-kokoro before retrying')
    with temporary_directory(prefix='.radio-kokoro-',dir=machine) as scratch:
        root=Path(scratch)/'radio-kokoro';root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(fetch(*runtime,ARCHIVE_MAX)),mode='r:gz') as archive:
            archive.extractall(root,filter='data')
        python=str(python_path(root))
        _check(run([python,'-I','-m','pip','install','--no-deps','--require-hashes','--only-binary',':all:',
                    '--no-cache-dir','--disable-pip-version-check','--no-input','-r',str(requirements())],INSTALL_TIMEOUT_S),
               'Kokoro package install')
        (root/'models').mkdir()
        for name,(url,digest) in MODEL_FILES.items():
            (root/'models'/name).write_bytes(fetch(url,digest,ARCHIVE_MAX))
        result=run([python,'-I',str(worker_script()),str(root/'models'),str(THREADS),'--check'],CHECK_TIMEOUT_S)
        _check(result,'Kokoro speech check')
        try:doc=json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError,ValueError) as exc:raise ValueError('Kokoro speech check returned no result') from exc
        if not isinstance(doc,dict) or doc.get('ok') is not True or not doc.get('audio_seconds',0)>0:
            raise ValueError('Kokoro speech check returned no audio')
        (root/MANIFEST).write_text(json.dumps(MANIFEST_DOC),encoding='utf8')
        os.replace(root,dest)
    return dict(version=1,kokoro='ready')


class KokoroSpeech:
    def __init__(self,machine,argv=None,ready_check=None,clock=time.monotonic):
        self.machine=Path(machine)
        self.argv=argv
        self.ready_check=ready_check or (lambda:ready(self.machine))
        self.clock=clock
        self._lock=threading.Lock()
        self._state=threading.Condition()
        self._proc=None;self._tree=None;self._loaded=False;self._reply=None;self._next=0;self._retry_at=float('-inf')
        self._closed=False
        self._cache=collections.OrderedDict();self._bytes=0

    def _command(self):
        if self.argv:return list(self.argv)
        root=self.machine/'radio-kokoro'
        return [str(python_path(root)),'-I',str(worker_script()),str(root/'models'),str(THREADS)]

    def _start(self):
        """Start the worker unless it runs. Needs no synthesis lock: the state lock guards it."""
        with self._state:
            if self._closed or (self._proc is not None and self._proc.poll() is None):return
            self._stop_locked()
            if self.clock()<self._retry_at or not self.ready_check():return
            self._retry_at=self.clock()+RETRY_S
            tree=ai_jobs.WindowsTree()
            flags={'creationflags':0x08004204} if os.name=='nt' else {'start_new_session':True}
            try:
                proc=subprocess.Popen(self._command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                      env=execution_env(),**flags)
                tree.attach(proc)
            except Exception:
                tree.close();raise
            self._proc,self._tree=proc,tree
            threading.Thread(target=self._read,args=(proc,),name='radio-kokoro',daemon=True).start()

    def prepare(self):
        try:self._start()
        except (OSError,ValueError):pass  # Warming is optional; synthesize reports a real failure.

    def _read(self,proc):
        try:
            while line:=proc.stdout.readline(JSON_MAX+1):
                if len(line)>JSON_MAX:break
                doc=json.loads(line)
                with self._state:
                    if proc is not self._proc:return
                    if doc.get('ready') is True:self._loaded=True
                    else:self._reply=doc
                    self._state.notify_all()
        except (OSError,ValueError,RecursionError,AttributeError):pass  # A broken stream ends this reader; waiters see the dead process.
        with self._state:self._state.notify_all()

    def _wait(self,done,stopped,deadline):
        with self._state:
            while not done():
                if stopped.is_set():raise ProviderError('cancelled')
                left=deadline-self.clock()
                if left<=0:raise ProviderError('timed_out')
                if self._proc is None or self._proc.poll() is not None:raise ProviderError('speech_failed')
                self._state.wait(min(left,.05))

    def synthesize(self,text,stopped,budget,voice='am_michael'):
        deadline=self.clock()+budget
        with self._lock:
            if stopped.is_set():raise ProviderError('cancelled')
            if not isinstance(text,str) or not text.strip() or len(text)>1200:raise ProviderError('invalid_text')
            ident=hashlib.sha256((voice+'\0'+text).encode()).hexdigest()
            if ident in self._cache:
                self._cache.move_to_end(ident)
                return self._cache[ident]
            if not self.ready_check():raise ProviderError('unconfigured')
            try:self._start()
            except (OSError,ValueError) as exc:raise ProviderError('speech_failed') from exc
            self._wait(lambda:self._loaded,stopped,deadline)
            with self._state:
                self._next+=1;request=self._next;self._reply=None
                try:
                    self._proc.stdin.write((json.dumps(dict(id=request,text=text,voice=voice))+'\n').encode())
                    self._proc.stdin.flush()
                except (OSError,AttributeError,ValueError) as exc:raise ProviderError('speech_failed') from exc
            # A reply to an abandoned request carries an older id and never matches.
            self._wait(lambda:(self._reply or {}).get('id')==request,stopped,deadline)
            with self._state:reply,self._reply=self._reply,None
            if not isinstance(reply.get('audio'),str):raise ProviderError('speech_failed')
            try:audio=base64.b64decode(reply['audio'],validate=True)
            except ValueError as exc:raise ProviderError('invalid_audio') from exc
            validate_wav(audio)
            self._cache[ident]=audio;self._bytes+=len(audio)
            while len(self._cache)>CACHE_ENTRIES or self._bytes>CACHE_MAX:
                _,old=self._cache.popitem(last=False);self._bytes-=len(old)
            return audio

    def _stop_locked(self):
        proc,tree,self._proc,self._tree,self._loaded=self._proc,self._tree,None,None,False
        if proc is not None:
            ai_jobs.terminate_tree(proc,tree)
            for stream in (proc.stdin,proc.stdout):
                try:stream.close()
                except OSError:pass  # The process is gone; a failed close changes nothing.
        elif tree is not None:tree.close()

    def close(self):
        with self._state:
            self._closed=True
            self._stop_locked()
            self._state.notify_all()


def smoke(machine,voice='am_michael',clock=time.monotonic,speech=None):
    """Two local syntheses: the first includes loading the model, the second does not."""
    speech=speech or KokoroSpeech(machine)
    stopped=threading.Event()
    try:
        start=clock();audio=speech.synthesize('Racecast driver radio speech check.',stopped,CHECK_TIMEOUT_S,voice=voice);cold=clock()-start
        start=clock();speech.synthesize('Fuel status is available for this stint.',stopped,CHECK_TIMEOUT_S,voice=voice);warm=clock()-start
    finally:
        speech.close()
    return dict(voice=voice,network_calls=0,audio_seconds=validate_wav(audio),cold_s=round(cold,3),warm_s=round(warm,3))


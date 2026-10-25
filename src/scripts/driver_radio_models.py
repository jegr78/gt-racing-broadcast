"""Explicit, pinned native-model provisioning. Relay startup never downloads files."""
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import tempfile
import time
import wave
import zipfile
from pathlib import Path,PurePosixPath

import http_util

MODELS={
 'en':('https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip','30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498'),
 'de':('https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip','b7e53c90b1f0a38456f4cd62b366ecd58803cd97cd42b06438e2c131713d5e43')}
WHISPER=('https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin','be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21')
LIBRARIES={'macos': ('https://files.pythonhosted.org/packages/44/19/5e8299237bc2005c3d155d3b48adba6fd6484465ad5c970302fc1d37947d/vosk-0.3.44-py3-none-macosx_10_6_universal2.whl', '029d0b3d6a5cff874c575b8a5814a4cccfb37608cff52e846c02a8c67a882801'), 'linux_arm64': ('https://files.pythonhosted.org/packages/a4/23/3130a69fa0bf4f5566a52e415c18cd854bf561547bb6505666a6eb1bb625/vosk-0.3.45-py3-none-manylinux2014_aarch64.whl', '54efb47dd890e544e9e20f0316413acec7f8680d04ec095c6140ab4e70262704'), 'linux_x64': ('https://files.pythonhosted.org/packages/fc/ca/83398cfcd557360a3d7b2d732aee1c5f6999f68618d1645f38d53e14c9ff/vosk-0.3.45-py3-none-manylinux_2_12_x86_64.manylinux2010_x86_64.whl', '25e025093c4399d7278f543568ed8cc5460ac3a4bf48c23673ace1e25d26619f'), 'windows': ('https://files.pythonhosted.org/packages/c0/4c/deb0861f7da9696f8a255f1731bb73e9412cca29c4b3888a3fcb2a930a59/vosk-0.3.45-py3-none-win_amd64.whl', '6994ddc68556c7e5730c3b6f6bad13320e3519b13ce3ed2aa25a86724e7c10ac')}


def target(system=None,machine=None):
    system=system or platform.system();machine=(machine or platform.machine()).lower()
    if system=='Darwin' and machine in ('arm64','aarch64','x86_64','amd64'):return 'macos'
    if system=='Windows' and machine in ('amd64','x86_64'):return 'windows'
    if system=='Linux' and machine in ('x86_64','amd64'):return 'linux_x64'
    if system=='Linux' and machine in ('aarch64','arm64'):return 'linux_arm64'
    raise ValueError('Native voice models do not support this OS/CPU')


def download(url,checksum,maximum,open_url=http_util.open_url):
    deadline=time.monotonic()+180
    raw=bytearray()
    with open_url(url,timeout=30) as response:
        while block:=response.read(min(1024*1024,maximum+1-len(raw))):
            raw.extend(block)
            if len(raw)>maximum or time.monotonic()>deadline:raise ValueError('Voice model download exceeded its bound')
    if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Voice model checksum mismatch')
    return bytes(raw)


def extract(raw,destination):
    total=0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members=archive.infolist()
        if len(members)>20000:raise ValueError('Voice archive contains too many entries')
        for member in members:
            # ZipInfo.filename normalizes backslashes on Windows and truncates
            # NULs. Validate the original name before any filesystem operation.
            name=member.orig_filename
            path=PurePosixPath(name)
            mode=member.external_attr>>16
            total+=member.file_size
            if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or '\0' in name
                    or any(part.endswith(('.', ' ')) for part in path.parts)
                    or len(name)>1024 or stat.S_ISLNK(mode)
                    or total>300*1024*1024 or member.file_size>128*1024*1024):
                raise ValueError('Unsafe voice model archive')
            out=Path(destination).joinpath(*path.parts)
            if member.is_dir():out.mkdir(parents=True,exist_ok=True);continue
            out.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(member) as source,out.open('wb') as stream:shutil.copyfileobj(source,stream,1024*1024)


def validate_phrases(root,fixtures=None):
    from driver_radio_recognition import Vosk
    from driver_radio_voice_config import PHRASES
    import sys
    source=Path(sys._MEIPASS)/'src' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    fixtures=Path(fixtures) if fixtures else source/'assets'/'driver-radio'
    library=next((Path(root)/'vosk'/name for name in ('libvosk.so','libvosk.dylib','libvosk.dll')
                  if (Path(root)/'vosk'/name).is_file()),None)
    if library is None:raise ValueError('Voice library is missing')
    for key,phrase in PHRASES.items():
        for name,expected in ((key.replace('_','-')+'.wav',True),('no-wake.wav',False)):
            rec=Vosk(library,Path(root)/'vosk-model-small-en-us-0.15',phrase=phrase)
            try:
                found=False
                with wave.open(str(fixtures/name),'rb') as wav:
                    if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,16000):raise ValueError('Invalid voice fixture')
                    while chunk:=wav.readframes(1280):found=rec.wake(chunk,phrase) or found
                if found!=expected:raise ValueError('Prepared wake phrase failed compatibility validation')
            finally:rec.close()
    return list(PHRASES)


def validate_in_worker(root):
    import sys
    import threading
    import ai_agents
    from driver_radio_process import run_process
    from driver_radio_voice_config import execution_env
    argv=([sys.executable,'_radio-model-check',str(root)] if getattr(sys,'frozen',False) else
          [sys.executable,str(Path(__file__)),str(root)])
    invocation=ai_agents.Invocation(argv,str(root),'',20,'speech')
    raw=run_process(invocation,None,threading.Event(),20,execution_env(),output_limit=4096,
                    extra_env={'PYINSTALLER_RESET_ENVIRONMENT':'1'} if getattr(sys,'frozen',False) else None)
    result=json.loads(raw)
    if result!=['hey_jeff','hey_engineer']:raise ValueError('Wake validation returned an invalid catalogue')
    return result


def provision(machine,whisper=False,fetch=download,validate=validate_in_worker,platform_key=None):
    from driver_radio_voice_config import ready
    machine=Path(machine);machine.mkdir(parents=True,exist_ok=True)
    dest=machine/'radio-voice'
    if dest.is_symlink():raise ValueError('Voice model directory must not be a link')
    if not ready(machine):
        if dest.exists():raise ValueError('Incomplete voice models exist; repair the local model directory before retrying')
        with tempfile.TemporaryDirectory(prefix='.radio-models-',dir=machine) as scratch:
            root=Path(scratch)/'radio-voice';root.mkdir()
            for url,digest in (LIBRARIES[platform_key or target()],*MODELS.values()):
                extract(fetch(url,digest,64*1024*1024),root)
            phrases=validate(root)
            (root/'validated-phrases.json').write_text(json.dumps(dict(version=1,phrases=phrases)),encoding='utf8')
            os.replace(root,dest)
    if whisper and not (dest/'ggml-tiny.bin').exists():
        raw=fetch(*WHISPER,100*1024*1024)
        with tempfile.NamedTemporaryFile(prefix='.whisper-',dir=dest,delete=False) as stream:
            temp=Path(stream.name);stream.write(raw)
        try:os.replace(temp,dest/'ggml-tiny.bin')
        finally:temp.unlink(missing_ok=True)
    return dict(version=1,models='ready',phrases=['hey_jeff','hey_engineer'],
                whisper_model=(dest/'ggml-tiny.bin').is_file(),
                whisper_cli=bool(shutil.which('whisper-cli')))


def main(argv=None):
    import sys
    import logsetup
    logsetup.harden_stdio()
    argv=sys.argv[1:] if argv is None else argv
    if len(argv)!=1:raise ValueError('Model validation requires one private directory')
    print(json.dumps(validate_phrases(argv[0])))
    return 0


if __name__=='__main__':raise SystemExit(main())

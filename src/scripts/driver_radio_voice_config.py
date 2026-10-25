"""Fixed local voice selections. No browser-supplied executable or model paths."""
import os
import json
import sys
from pathlib import Path

PHRASES={'hey_jeff':'hey jeff','hey_engineer':'hey engineer'}
DEFAULTS=dict(wake='hey_jeff',stt='vosk',volume=.8,muted=False)


def settings(value=None):
    if value is not None and (not isinstance(value,dict) or set(value)-set(DEFAULTS)):
        raise ValueError('Invalid native voice settings')
    result=dict(DEFAULTS,**(value or {}))
    if (not isinstance(result['wake'],str) or result['wake'] not in PHRASES or result['stt'] not in ('vosk','whisper','google')
            or type(result['volume']) not in (int,float) or not 0<=result['volume']<=1
            or type(result['muted']) is not bool):
        raise ValueError('Invalid native voice settings')
    return result


def paths(machine):
    root=Path(machine)/'radio-voice'
    name='libvosk.dll' if sys.platform.startswith('win') else 'libvosk.dylib' if sys.platform=='darwin' else 'libvosk.so'
    return dict(library=str(root/'vosk'/name),en=str(root/'vosk-model-small-en-us-0.15'),
                de=str(root/'vosk-model-small-de-0.15'))


def ready(machine):
    p=paths(machine);root=Path(machine)/'radio-voice';manifest=root/'validated-phrases.json'
    try:
        if root.is_symlink() or manifest.is_symlink() or any(Path(value).is_symlink() for value in p.values()):return False
        with manifest.open('rb') as stream:raw=stream.read(2049)
        if len(raw)>2048 or json.loads(raw)!=dict(version=1,phrases=list(PHRASES)):return False
        return Path(p['library']).is_file() and all(Path(p[language]).is_dir() for language in ('en','de'))
    except (OSError,ValueError,RecursionError):return False


def execution_env():
    import ai_jobs
    env=ai_jobs.execution_env(os.environ)
    if getattr(sys,'frozen',False):env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    return env

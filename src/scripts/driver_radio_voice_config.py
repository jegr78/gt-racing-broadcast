"""Fixed local voice selections. No browser-supplied executable or model paths."""
import os
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
    p=paths(machine)
    return (Path(p['library']).is_file() and Path(p['en']).is_dir()
            and (Path(machine)/'radio-voice'/'validated-phrases.json').is_file())


def execution_env():
    import ai_jobs
    env=ai_jobs.execution_env(os.environ)
    if getattr(sys,'frozen',False):env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    return env

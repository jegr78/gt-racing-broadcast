#!/usr/bin/env python3
"""Provisioning is explicit, hash checked, bounded and validates wake phrases."""
import hashlib,io,json,sys,tempfile,zipfile,stat
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_models as m
from driver_radio_voice_config import paths,ready


def archive(name,data=b'fixture'):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr(name,data)
    return stream.getvalue()


def t_explicit_provision_validates_before_publishing_and_reuses_installed_models():
    with tempfile.TemporaryDirectory() as d:
        calls=[];validated=[]
        library=Path(paths(d)['library']).name
        def fetch(url,digest,maximum):
            calls.append(url)
            assert len(digest)==64 and maximum<=100*1024*1024
            if url.endswith('.whl'):return archive('vosk/'+library)
            if '-en-' in url:return archive('vosk-model-small-en-us-0.15/conf/model.conf')
            if '-de-' in url:return archive('vosk-model-small-de-0.15/conf/model.conf')
            return b'whisper weights'
        def validate(root):
            assert (root/'vosk'/library).exists() and not (Path(d)/'radio-voice').exists()
            validated.append(root)
            return ['hey_jeff','hey_engineer']
        result=m.provision(d,fetch=fetch,validate=validate,platform_key='linux_x64')
        assert result['models']=='ready' and ready(d) and len(calls)==3 and len(validated)==1
        assert json.loads((Path(d)/'radio-voice'/'validated-phrases.json').read_text())['version']==1
        m.provision(d,fetch=fetch,validate=validate,platform_key='linux_x64')
        assert len(calls)==3
        result=m.provision(d,whisper=True,fetch=fetch,validate=validate,platform_key='linux_x64')
        assert result['whisper_model'] and len(calls)==4
        (Path(d)/'radio-voice'/'validated-phrases.json').write_text('{}')
        assert not ready(d), 'Corrupt phrase validation metadata must fail closed'


def t_corrupt_download_unsafe_archives_and_failed_validation_publish_nothing():
    class Reply:
        def __init__(self):self.stream=io.BytesIO(b'bad archive')
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return self.stream.read(n)
    for checksum,maximum in (('0'*64,1024),(hashlib.sha256(b'bad archive').hexdigest(),2)):
        try:m.download('https://fixture.invalid/archive',checksum,maximum,open_url=lambda *a,**k:Reply())
        except ValueError:pass  # The unsafe input or failed cleanup must be rejected.
        else:raise AssertionError('Unverified download accepted')
    for name in ('../escape','/absolute','C:/escape','a\\escape'):
        with tempfile.TemporaryDirectory() as d:
            # Invalid-archive fault injection must never write outside the fixture.
            with patch.object(Path,'mkdir',side_effect=AssertionError('Unexpected archive directory write')), \
                 patch.object(Path,'open',side_effect=AssertionError('Unexpected archive file write')):
                try:m.extract(archive(name),d)
                except ValueError:pass  # The unsafe input or failed cleanup must be rejected.
                else:raise AssertionError('Unsafe archive accepted')
            assert list(Path(d).iterdir())==[]
    with tempfile.TemporaryDirectory() as d:
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            info=zipfile.ZipInfo('linked');info.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(info,'target')
        try:m.extract(stream.getvalue(),d)
        except ValueError:pass  # The unsafe input or failed cleanup must be rejected.
        else:raise AssertionError('Linked model file accepted')
    with tempfile.TemporaryDirectory() as d:
        def invalid(root):raise ValueError('Wake fixture failed')
        try:m.provision(d,fetch=lambda *a:archive('fixture'),validate=invalid,platform_key='linux_x64')
        except ValueError:pass  # The unsafe input or failed cleanup must be rejected.
        else:raise AssertionError('Unvalidated wake catalogue published')
        assert not (Path(d)/'radio-voice').exists()


def t_frozen_phrase_validation_reads_the_bundled_source_fixture_directory():
    import wave
    import driver_radio_recognition as recognition
    class Recognizer:
        def __init__(self,*args,**kwargs):pass
        def wake(self,pcm,phrase):return pcm==b'\1\0'
        def close(self):pass
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);models=root/'models';(models/'vosk').mkdir(parents=True)
        (models/'vosk'/'libvosk.so').write_bytes(b'fixture library')
        fixture=root/'bundle'/'src'/'assets'/'driver-radio';fixture.mkdir(parents=True)
        for name,pcm in (('hey-jeff',b'\1\0'),('hey-engineer',b'\1\0'),('no-wake',b'\0\0')):
            with wave.open(str(fixture/(name+'.wav')),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(root/'bundle'),create=True),patch.object(recognition,'Vosk',Recognizer):
            assert m.validate_phrases(models)==['hey_jeff','hey_engineer']


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

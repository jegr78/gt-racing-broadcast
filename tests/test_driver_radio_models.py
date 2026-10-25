#!/usr/bin/env python3
"""Provisioning is explicit, hash checked, bounded and validates wake phrases."""
import hashlib,io,json,sys,tempfile,zipfile,stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_models as m
from driver_radio_voice_config import paths,ready


def archive(name,data=b'fixture'):
    stream=io.BytesIO()
    # ZipInfo normalizes backslashes on Windows. Preserve the actual archive name.
    info=zipfile.ZipInfo(name);info.filename=name
    with zipfile.ZipFile(stream,'w') as z:z.writestr(info,data)
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
    for name in ('../escape','/absolute','C:/escape','a\\escape','.. /escape','a./escape','a\0/escape'):
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


def t_archive_rejects_original_names_even_when_the_reader_normalizes_them():
    class NormalizedArchive:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def infolist(self):
            return [SimpleNamespace(filename='a/escape',orig_filename='a\\escape',external_attr=0,file_size=1,is_dir=lambda:False)]
    with tempfile.TemporaryDirectory() as d, \
         patch.object(m,'zipfile',SimpleNamespace(ZipFile=lambda _:NormalizedArchive())), \
         patch.object(Path,'mkdir',side_effect=AssertionError('Unexpected archive directory write')), \
         patch.object(Path,'open',side_effect=AssertionError('Unexpected archive file write')):
        try:m.extract(b'fixture',d)
        except ValueError:pass  # Reject the raw name before any filesystem operation.
        else:raise AssertionError('Normalized unsafe archive name accepted')


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


def t_macos_library_matches_the_pinned_upstream_wheel():
    import driver_radio_voice_config as config
    import driver_radio_recognition as recognition
    class Recognizer:
        def __init__(self,library,*args,**kwargs):assert Path(library).name=='libvosk.dyld'
        def wake(self,pcm,phrase):return pcm==b'\1\0'
        def close(self):pass
    import wave
    with tempfile.TemporaryDirectory() as d,patch.object(sys,'platform','darwin'):
        root=Path(d)/'radio-voice';(root/'vosk').mkdir(parents=True)
        (root/'vosk/libvosk.dyld').write_bytes(b'fixture')
        for language in ('en','de'):Path(config.paths(d)[language]).mkdir()
        (root/'validated-phrases.json').write_text(json.dumps(dict(version=1,phrases=list(config.PHRASES))))
        assert Path(config.paths(d)['library']).name=='libvosk.dyld'
        assert config.ready(d)
        fixtures=Path(d)/'fixtures';fixtures.mkdir()
        for name,pcm in (('hey-jeff',b'\1\0'),('hey-engineer',b'\1\0'),('no-wake',b'\0\0')):
            with wave.open(str(fixtures/(name+'.wav')),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
        with patch.object(recognition,'Vosk',Recognizer):assert m.validate_phrases(root,fixtures)==list(config.PHRASES)


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

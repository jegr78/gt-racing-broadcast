#!/usr/bin/env python3
"""Profile-owned grants and every-read access checks using synthetic recordings."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import race_engineer_access as access
import test_ai_package as fixture
import gt7_context


def rejected(fn,code):
    try:fn()
    except access.AccessError as exc:assert exc.code==code,(exc.code,code)
    else:raise AssertionError('Expected '+code)


def t_default_off_and_current_authorization():
    with tempfile.TemporaryDirectory() as d:
        store=access.Store(Path(d)/'profile',available=True)
        assert not store.read()['enabled']
        rejected(lambda:store.authorize({'race_engineer'}),'feature_disabled')
        store.update(enabled=True,expected=0)
        assert store.authorize({'race_engineer'})['enabled']
        rejected(lambda:access.Store(Path(d)/'profile',available=False).authorize({'race_engineer'}),'source_unavailable')
        for roles in (set(),{'director'},{'commentator'},{'race_control'}):
            rejected(lambda roles=roles:store.authorize(roles),'forbidden')
        store.update(enabled=False,expected=1)
        rejected(lambda:store.authorize({'race_engineer'}),'feature_disabled')
        rejected(lambda:access.Store(Path(d)/'other',available=False).update(enabled=True,expected=0),'source_unavailable')


def t_grants_are_scoped_and_context_changes_do_not_revoke():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile';source=fixture.source(root/'telemetry-recordings')
        store=access.Store(root,available=True)
        state=store.update(enabled=True,grant=source.identity,label='Practice reference',expected=0)
        assert state['revision']==1
        assert store.reference(source.identity,{'race_engineer'})==Path(source.path)
        public=store.authorize({'race_engineer'})
        assert public['references']==[{'id':source.identity,'label':'Practice reference','context_shared':True}]
        assert source.name not in json.dumps(public) and str(root) not in json.dumps(public)
        ctx=gt7_context.Store.for_recording(source.path)
        ctx.save({'notes':[],'sessions':{}},expected=1)
        assert store.reference(source.identity,{'race_engineer'})==Path(source.path)
        other=access.Store(Path(d)/'other',available=True);other.update(enabled=True,expected=0)
        rejected(lambda:other.reference(source.identity,{'race_engineer'}),'reference_unavailable')
        rejected(lambda:store.update(grant='../private',expected=1),'invalid_reference')
        rejected(lambda:store.update(grant='f'*32,expected=1),'reference_unavailable')
        store.update(revoke=source.identity,expected=1)
        rejected(lambda:store.reference(source.identity,{'race_engineer'}),'reference_unavailable')


def t_deleted_replaced_and_duplicate_sources_never_inherit_grants():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile';source=fixture.source(root/'telemetry-recordings')
        store=access.Store(root,available=True);store.update(enabled=True,grant=source.identity,expected=0)
        path=Path(source.path);original=path.read_bytes()
        path.unlink()
        rejected(lambda:store.reference(source.identity,{'race_engineer'}),'reference_unavailable')
        path.write_bytes(original+b'changed')
        rejected(lambda:store.reference(source.identity,{'race_engineer'}),'reference_unavailable')
        assert store.authorize({'race_engineer'})['references']==[]
        store.update(grant=source.identity,expected=1)
        (path.parent/'duplicate.gt7rec').write_bytes(path.read_bytes())
        assert store.reference(source.identity,{'race_engineer'})==path
        rejected(lambda:store.update(grant=source.identity,expected=2),'reference_unavailable')


def t_revision_conflicts_and_invalid_state_fail_closed():
    with tempfile.TemporaryDirectory() as d:
        store=access.Store(Path(d),available=True)
        store.update(enabled=True,expected=0)
        rejected(lambda:store.update(enabled=False,expected=0),'conflict')
        assert store.read()['enabled']
        rejected(lambda:store.update(enabled='yes',expected=1),'invalid_settings')
        store.path.write_text('{"enabled":true,"grants":"anything"}')
        rejected(lambda:store.authorize({'race_engineer'}),'invalid_settings')


def t_recording_identity_cannot_change_under_restored_file_metadata():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile';source=fixture.source(root/'telemetry-recordings')
        store=access.Store(root,available=True);store.update(enabled=True,grant=source.identity,expected=0)
        path=Path(source.path);st=path.stat();raw=path.read_bytes()
        assert source.identity.encode() in raw
        path.write_bytes(raw.replace(source.identity.encode(),b'f'*len(source.identity)))
        os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns))
        rejected(lambda:store.reference(source.identity,{'race_engineer'}),'reference_unavailable')


def t_document_archive_and_grant_limits_are_finite():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile';store=access.Store(root,available=True)
        sources=[fixture.source(root/'telemetry-recordings') for _ in range(3)]
        with patch.object(access,'MAX_GRANTS',2):
            store.update(grant=sources[0].identity,expected=0)
            store.update(grant=sources[1].identity,expected=1)
            rejected(lambda:store.update(grant=sources[2].identity,expected=2),'grant_limit')
            assert store.read()['revision']==2
        with patch.object(access,'MAX_RECORDINGS',2):
            rejected(store.recordings,'archive_limit')
        doc=store.path.read_text();store.path.write_text(doc+' '*131073)
        rejected(store.read,'invalid_settings')


def t_settings_paths_cannot_point_at_another_profile():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile';other=fixture.source(Path(d)/'other/telemetry-recordings')
        store=access.Store(root,available=True);store.update(enabled=True,expected=0)
        st=Path(other.path).stat();doc=store.read()
        doc['grants'][other.identity]=dict(name='../other/telemetry-recordings/'+other.name,
            label='Foreign source',signature=[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns])
        store.path.write_text(json.dumps(doc))
        rejected(lambda:store.authorize({'race_engineer'}),'invalid_settings')


def t_linked_recording_root_cannot_reuse_another_profiles_grants():
    with tempfile.TemporaryDirectory() as d:
        other=fixture.source(Path(d)/'other/telemetry-recordings')
        store=access.Store(Path(d)/'profile',available=True);store.update(enabled=True,expected=0)
        try:store.recdir.symlink_to(Path(other.path).parent,target_is_directory=True)
        except OSError:
            if os.name!='nt':raise
            subprocess.run(['cmd','/c','mklink','/J',str(store.recdir),str(Path(other.path).parent)],
                check=True,capture_output=True,encoding='utf8',errors='replace')
        st=Path(other.path).stat();doc=store.read()
        doc['grants'][other.identity]=dict(name=other.name,label='Foreign reference',
            signature=[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns])
        store.path.write_text(json.dumps(doc))
        rejected(lambda:store.authorize({'race_engineer'}),'invalid_settings')


def t_replaced_sources_are_rejected_before_header_parsing():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile';source=fixture.source(root/'telemetry-recordings')
        store=access.Store(root,available=True);store.update(enabled=True,grant=source.identity,expected=0)
        with Path(source.path).open('ab') as f:f.write(b'changed')
        with patch.object(gt7_context,'source_identity',side_effect=AssertionError('Changed source must not be parsed')):
            rejected(lambda:store.reference(source.identity,{'race_engineer'}),'reference_unavailable')


def t_source_replacement_between_metadata_checks_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'profile';source=fixture.source(root/'telemetry-recordings')
        store=access.Store(root,available=True);store.update(enabled=True,grant=source.identity,expected=0)
        original=store._source
        def changed(name):
            with Path(source.path).open('ab') as f:f.write(b'changed')
            return original(name)
        with patch.object(store,'_source',side_effect=changed):
            rejected(lambda:store.reference(source.identity,{'race_engineer'}),'reference_unavailable')


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

#!/usr/bin/env python3
"""Private compact coaching journals use real files without recordings or providers."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src/scripts'),str(Path(__file__).parent)]
import driver_coach_history as history
from test_driver_coach_service import Fixture,LOCAL,REMOTE,rejects

GENERATION='a'*32


def sample(root):
    f=Fixture(Path(root)/'source');f.command();f.step()
    for n in (2,3,4):f.complete(n);f.step()
    state=f.service.view(LOCAL)
    record=history.compact(state,f.snap['current'],GENERATION,1000,1200)
    return f,record


def t_compact_history_survives_without_a_recording_or_private_raw_input():
    with tempfile.TemporaryDirectory() as root:
        f,record=sample(root)
        store=history.Store(Path(root)/'solo',clock=lambda:1200)
        assert not store.directory.exists(),'A constructor touched persistence before relay bind'
        assert store.save(LOCAL,record)
        result=store.read(LOCAL,GENERATION)
        assert result['revision']==1 and len(result['entries'])==1
        saved=result['entries'][0]
        assert saved['baseline']['lap_ids']==f.service.view(LOCAL)['focus']['baseline']['lap_ids']
        assert saved['focus']['action']==f.service.view(LOCAL)['focus']['focus']['action']
        assert saved['progress']['result']=='provisional' and 'unknown' in saved['progress']['text']
        assert saved['latest']['lap_id']=='lap-4' and saved['context']['car_id']==1
        raw=next(store.directory.glob('*.json')).read_text(encoding='utf8')
        for field in ('"trace"','"audio"','"transcript"','"points"','"credentials"','"provider"'):
            assert field not in raw,field
        assert len(raw.encode())<history.MAX_BYTES
        assert history.Store(Path(root)/'solo',clock=lambda:1200).read(LOCAL,GENERATION)==result
        if os.name!='nt':
            assert store.directory.stat().st_mode & 0o077==0
            assert next(store.directory.glob('*.json')).stat().st_mode & 0o077==0


def t_owner_profile_and_namespace_are_exact_and_never_selected_from_a_filename():
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root);store=history.Store(Path(root)/'solo',clock=lambda:1200)
        assert store.save(LOCAL,record)
        assert store.read(REMOTE,GENERATION)['entries']==[]
        assert history.Store(Path(root)/'other',clock=lambda:1200).read(dict(LOCAL,profile='other'),GENERATION)['entries']==[]
        for owner in (dict(LOCAL,profile='other'),dict(LOCAL,subject='../../elsewhere'),dict(LOCAL,path='elsewhere')):
            rejects(lambda owner=owner:store.read(owner,GENERATION),'invalid_owner')
        long_owner=dict(LOCAL,subject='console:'+'x'*160)
        assert store.save(long_owner,record) and store.read(long_owner,GENERATION)['entries']


def t_restart_marks_only_prior_generation_unfinished_work_as_partial():
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root);store=history.Store(Path(root)/'solo',clock=lambda:1200)
        store.save(LOCAL,record)
        active=store.read(LOCAL,GENERATION)['entries'][0]
        assert active['status']=='active' and active['ended_at'] is None
        restarted=store.read(LOCAL,'b'*32)['entries'][0]
        assert restarted['status']=='interrupted' and restarted['reason']=='relay_interrupted'
        assert restarted['baseline']==active['baseline'] and restarted['progress']==active['progress']
        assert not store.save(LOCAL,record),'An obsolete checkpoint revived a completed segment'


def t_deletion_is_revisioned_owner_only_and_an_active_checkpoint_cannot_resurrect():
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root);store=history.Store(Path(root)/'solo',clock=lambda:1200)
        store.save(LOCAL,record);current=store.read(LOCAL,GENERATION)
        rejects(lambda:store.delete(LOCAL,current['revision']-1,record['id']),'conflict')
        rejects(lambda:store.delete(REMOTE,current['revision'],record['id']),'conflict')
        deleted=store.delete(LOCAL,current['revision'],record['id'])
        assert deleted['entries']==[] and not store.save(LOCAL,record)
        fresh=copy.deepcopy(record);fresh['id']='c'*32;fresh['training_id']='d'*32
        assert store.save(LOCAL,fresh)
        assert store.delete(LOCAL,store.read(LOCAL,GENERATION)['revision'])['entries']==[]
        assert not store.save(LOCAL,fresh)


def t_retention_is_bounded_and_old_entries_are_pruned_without_changing_the_measurements():
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root);clock=[1200];store=history.Store(Path(root)/'solo',clock=lambda:clock[0])
        for n in range(40):
            value=copy.deepcopy(record);value.update(id=f'{n:032x}',updated_at=1200+n)
            assert store.save(LOCAL,value)
        entries=store.read(LOCAL,GENERATION)['entries']
        assert len(entries)==32 and {r['id'] for r in entries}=={f'{n:032x}' for n in range(8,40)}
        assert all(r['baseline']==record['baseline'] for r in entries)
        clock[0]+=91*86400
        assert store.read(LOCAL,GENERATION)['entries']==[]


def t_corrupt_oversized_and_symlinked_private_files_fail_closed():
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root);store=history.Store(Path(root)/'solo',clock=lambda:1200)
        store.save(LOCAL,record);path=next(store.directory.glob('*.json'));original=path.read_bytes()
        for raw in (b'{broken',b' '* (history.MAX_BYTES+1),json.dumps({'owner':LOCAL,'entries':[{'trace':'secret'}]}).encode()):
            path.write_bytes(raw)
            rejects(lambda:store.read(LOCAL,GENERATION),'history_unavailable')
        path.write_bytes(original)
        if os.name!='nt':
            elsewhere=Path(root)/'unrelated';elsewhere.write_text('private',encoding='utf8')
            path.unlink();path.symlink_to(elsewhere)
            rejects(lambda:store.read(LOCAL,GENERATION),'history_unavailable')
            assert elsewhere.read_text(encoding='utf8')=='private'


if __name__=='__main__':
    tests=[(name,fn) for name,fn in globals().copy().items() if name.startswith('t_') and callable(fn)]
    for name,fn in tests:fn();print('PASS',name)
    print('PASS',len(tests),'private history scenarios')

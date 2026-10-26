#!/usr/bin/env python3
"""Private compact coaching journals use real files without recordings or providers."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

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
        same_training=copy.deepcopy(record);same_training['id']='e'*32
        assert not store.save(LOCAL,same_training),'A context reset resumed deleted active training retention'
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
        nested=json.loads(original);nested['entries'][0]['worked_focus']=[{}]
        for raw in (b'{broken',b' '* (history.MAX_BYTES+1),json.dumps({'owner':LOCAL,'entries':[{'trace':'secret'}]}).encode(),json.dumps(nested).encode()):
            path.write_bytes(raw)
            rejects(lambda:store.read(LOCAL,GENERATION),'history_unavailable')
        path.write_bytes(original)
        if os.name!='nt':
            elsewhere=Path(root)/'unrelated';elsewhere.write_text('private',encoding='utf8')
            path.unlink();path.symlink_to(elsewhere)
            rejects(lambda:store.read(LOCAL,GENERATION),'history_unavailable')
            assert elsewhere.read_text(encoding='utf8')=='private'


def coordinated(root):
    import driver_coach_service as service
    f=Fixture(Path(root)/'source')
    store=history.Store(Path(root)/'solo',clock=lambda:f.wall)
    f.service=service.Service('solo',lambda:copy.deepcopy(f.snap),f.detail,f.radio.service,
                              clock=lambda:f.at,wall=lambda:f.wall,journal=store)
    return f,store


def t_coordinator_finalizes_private_work_and_context_segments_without_reusing_old_calibration():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3,4):f.complete(n);f.step()
        before=store.read(LOCAL,f.service._generation)['entries'][0]
        assert before['focus'] and before['latest']['lap_id']=='lap-4'
        f.command('stop');f.step()
        ended=store.read(LOCAL,f.service._generation)['entries'][0]
        assert ended['status']=='stopped' and ended['baseline']==before['baseline']
        f.command();state=f.step()
        assert not state['calibration']['calibration_ids'] and state['focus']['focus'] is None
        for n in (6,7):f.complete(n);f.step()
        f.snap['current']['car_id']=99;f.step()
        entries=store.read(LOCAL,f.service._generation)['entries']
        assert any(r['status']=='invalidated' and r['reason']=='live_context_changed' for r in entries)


def t_blocked_checkpoint_cannot_block_stop_or_revive_deleted_private_work():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        initial=store.read(LOCAL,f.service._generation)
        entered=threading.Event();release=threading.Event();original=store.save
        def blocked(*args):
            entered.set();assert release.wait(5);return original(*args)
        f.complete(4)
        with patch.object(store,'save',blocked):
            worker=threading.Thread(target=f.service.step);worker.start()
            try:
                assert entered.wait(2)
                state=f.command('stop');assert not state['active'] and state['pending']
                assert store.delete(LOCAL,initial['revision'])['entries']==[]
            finally:release.set();worker.join(5)
        assert not worker.is_alive()
        f.step();assert store.read(LOCAL,f.service._generation)['entries']==[]


def t_graceful_worker_close_finishes_the_last_stored_checkpoint():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        f.service.start()
        f.service.close()
        record=store.read(LOCAL,f.service._generation)['entries'][0]
        assert record['status']=='stopped' and record['reason']=='relay_closed'


def t_unchanged_worker_polls_do_not_rewrite_history_or_make_deletion_revisions_unusable():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        before=store.read(LOCAL,f.service._generation)
        for _ in range(4):f.at+=1;f.wall+=1;f.step()
        after=store.read(LOCAL,f.service._generation)
        assert after==before,'Unchanged worker polls rewrote the compact checkpoint'


def t_history_display_rechecks_shared_reference_rights_and_marks_corrected_sources():
    import driver_coach_service as service
    with tempfile.TemporaryDirectory() as root:
        f,record=sample(root)
        record['reference']=dict(id='r:'+('a'*32)+':1:20',source='selected')
        record['reference_fingerprint']='b'*64
        record['source_fingerprints']={'lap-4':history.fingerprint(service._facts(f.snap['laps'][-1]))}
        doc=dict(revision=1,entries=[record])
        visible=history.expose(doc,f.snap,service._facts,lambda _ident,_fingerprint:False)
        entry=visible['entries'][0]
        assert entry['evidence_status']=='reference_unavailable' and entry['reference'] is None and entry['focus'] is None
        assert entry['latest']['observation']==record['latest']['observation'] and entry['latest'].get('action') is None
        assert doc['entries'][0]['focus'],'Display filtering modified stored historical facts'
        f.snap['laps'][-1]['measurement_hash']='corrected'
        corrected=history.expose(doc,f.snap,service._facts,lambda _ident,_fingerprint:True)['entries'][0]
        assert corrected['evidence_status']=='corrected' and corrected['baseline'] is None and corrected['progress'] is None


def t_history_suggestion_requires_compatible_current_calibration_and_never_supplies_old_measurements():
    import driver_coach_service as service
    with tempfile.TemporaryDirectory() as root:
        f,record=sample(root);record.update(status='stopped',ended_at=1200)
        record['context'].update(settings_confirmed=True,context_settings={})
        current=copy.deepcopy(record['context']);state=f.service.view(LOCAL)
        state['calibration']['accepted']=True;state['calibration']['training_id']='f'*32
        exposed=history.expose(dict(revision=1,entries=[record]),f.snap,service._facts,lambda _id,_hash:True)
        suggestion=history.suggest(exposed,state,current)
        assert suggestion and set(suggestion)=={'id','skill','text'} and suggestion['skill']==record['focus']['skill']
        assert 'calibration' in suggestion['text'].lower()
        before=copy.deepcopy(state)
        for field,value in (('car_id',99),('track_id','different'),('definition_fingerprint','changed'),('settings_confirmed',False)):
            changed=dict(current);changed[field]=value
            assert history.suggest(exposed,state,changed) is None,field
        state['calibration']['accepted']=False
        assert history.suggest(exposed,state,current) is None
        assert before['calibration']['baseline']==state['calibration']['baseline']


if __name__=='__main__':
    tests=[(name,fn) for name,fn in globals().copy().items() if name.startswith('t_') and callable(fn)]
    for name,fn in tests:fn();print('PASS',name)
    print('PASS',len(tests),'private history scenarios')

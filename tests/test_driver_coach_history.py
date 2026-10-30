#!/usr/bin/env python3
"""Private compact coaching journals use real files without recordings or providers."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
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
        store._path(REMOTE).write_bytes(store._path(LOCAL).read_bytes())
        rejects(lambda:store.read(REMOTE,GENERATION),'history_unavailable')
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
        cleared=store.delete(LOCAL,store.read(LOCAL,GENERATION)['revision'])
        assert cleared['entries']==[]
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
        wrong_reference=json.loads(original);wrong_reference['entries'][0]['reference']['id']=[]
        wrong_progress=json.loads(original);wrong_progress['entries'][0]['progress']['sample_count']='many'
        extra_record=json.loads(original);extra_record['entries'][0]['unused']=1
        extra_doc=json.loads(original);extra_doc['unused']=1
        oversized_record=json.loads(original);oversized_record['entries'][0]['context']['context_settings']={'extra':['x'*1000]*10}
        nonfinite=json.loads(original);nonfinite['entries'][0]['context']['context_settings']={'extra':float('nan')}
        wrong_id=json.loads(original);wrong_id['entries'][0]['id']='../private'
        boolean_count=json.loads(original);boolean_count['entries'][0]['progress']['sample_count']=True
        for raw in (b'{broken',original+b' '*history.MAX_BYTES,json.dumps({'owner':LOCAL,'entries':[{'trace':'secret'}]}).encode(),
                    *[json.dumps(value).encode() for value in (nested,wrong_reference,wrong_progress,extra_record,extra_doc,
                                                             oversized_record,nonfinite,wrong_id,boolean_count)]):
            path.write_bytes(raw)
            rejects(lambda:store.read(LOCAL,GENERATION),'history_unavailable')
        path.write_bytes(original)
        if os.name!='nt':
            elsewhere=Path(root)/'unrelated';elsewhere.write_bytes(original)
            path.unlink();path.symlink_to(elsewhere)
            rejects(lambda:store.read(LOCAL,GENERATION),'history_unavailable')
            assert elsewhere.read_bytes()==original


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
                deleted=store.delete(LOCAL,initial['revision'])
                assert deleted['entries']==[]
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
        same_training=copy.deepcopy(state);same_training['calibration']['training_id']=record['training_id']
        assert history.suggest(exposed,same_training,current) is None
        unknown=copy.deepcopy(exposed);unknown['entries'][0]['context']['settings_confirmed']=False
        assert history.suggest(unknown,state,dict(current,settings_confirmed=False)) is None
        for field,value in (('car_id',99),('track_id','different'),('definition_fingerprint','changed'),('settings_confirmed',False)):
            changed=dict(current);changed[field]=value
            assert history.suggest(exposed,state,changed) is None,field
        state['calibration']['accepted']=False
        assert history.suggest(exposed,state,current) is None
        assert before['calibration']['baseline']==state['calibration']['baseline']


def t_many_worked_exercises_cannot_make_the_journal_write_a_record_it_cannot_read():
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root);store=history.Store(Path(root)/'solo',clock=lambda:1200)
        for n in range(16):
            value=copy.deepcopy(record)
            value['focus'].update(id='exercise/'+str(n),label='L'*160,action='A'*600)
            value['worked_focus']=[copy.deepcopy(value['focus'])];value['updated_at']=1200+n
            assert store.save(LOCAL,value)
            saved=store.read(LOCAL,GENERATION)['entries'][0]
            assert saved['worked_focus'][-1]['id']=='exercise/'+str(n)
            assert 1<=len(saved['worked_focus'])<=8


def t_retaining_the_last_review_across_a_control_also_retains_its_correction_proof():
    import driver_coach_service as service
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3,4):f.complete(n);f.step()
        f.command('skill',skill='braking');f.step()
        document=store.read(LOCAL,f.service._generation)
        assert document['entries'][0]['latest']['lap_id']=='lap-4'
        f.snap['laps'][-1]['measurement_hash']='corrected-after-skill-change'
        exposed=history.expose(document,f.snap,service._facts,lambda _id,_digest:True)
        assert exposed['entries'][0]['evidence_status']=='corrected'
        assert exposed['entries'][0]['latest'] is None


def t_a_failed_checkpoint_keeps_live_feedback_and_finishes_as_an_honest_partial_debrief():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3):f.complete(n);f.step()
        f.complete(4)
        with patch.object(store,'save',side_effect=OSError('Synthetic disk failure')):
            current=f.step()
        assert current['active'] and current['latest']['lap_id']=='lap-4'
        assert store.read(LOCAL,f.service._generation)['entries'][0]['latest']['lap_id']=='lap-3'
        f.command('stop');f.step()
        saved=store.read(LOCAL,f.service._generation)['entries'][0]
        assert saved['status']=='interrupted' and saved['reason']=='checkpoint_unavailable'
        assert saved['latest']['lap_id']=='lap-3'


def t_a_late_checkpoint_cannot_replace_newer_progress_or_a_finished_segment():
    with tempfile.TemporaryDirectory() as root:
        _,old=sample(root);store=history.Store(Path(root)/'solo',clock=lambda:1200)
        assert store.save(LOCAL,old)
        newer=copy.deepcopy(old);newer['updated_at']+=1;newer['progress']['sample_count']=2
        assert store.save(LOCAL,newer) and not store.save(LOCAL,old)
        assert store.read(LOCAL,GENERATION)['entries'][0]['progress']['sample_count']==2
        store.finish(LOCAL,old['id'])
        assert not store.save(LOCAL,newer)


def t_a_linked_history_directory_cannot_change_an_unrelated_directory_permissions():
    if os.name=='nt':return  # Windows CI verifies ordinary files; creating a symlink requires host privileges.
    with tempfile.TemporaryDirectory() as root:
        other=Path(root)/'unrelated';other.mkdir(mode=0o755)
        profile=Path(root)/'solo';profile.mkdir();(profile/'coach-history').symlink_to(other,target_is_directory=True)
        before=other.stat().st_mode
        rejects(lambda:history.Store(profile).read(LOCAL,GENERATION),'history_unavailable')
        assert other.stat().st_mode==before


def t_final_source_proof_removes_stale_facts_without_callbacks_or_restoring_a_masked_view():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3,4):f.complete(n);f.step()
        before=f.service.view(LOCAL);assert before['focus']['focus']
        for change in ('context','measurement'):
            doc=copy.deepcopy(f.snap)
            if change=='context':doc['current']['car_id']=99
            else:doc['laps'][-1]['measurement_hash']='corrected-during-final-IO'
            with patch.object(f.service,'source',side_effect=AssertionError('Final source proof performed external IO')):
                result=f.service.after_io(LOCAL,before,doc)
            assert result['phase']=='paused' and result['focus'] is None and result['calibration'] is None
        masked=dict(before,phase='paused',calibration=None,focus=None,latest=None)
        assert f.service.after_io(LOCAL,masked,copy.deepcopy(f.snap))['focus'] is None
        f.command('stop')
        stopped=f.service.after_io(LOCAL,before,copy.deepcopy(f.snap))
        assert not stopped['active'] and stopped['focus'] is None


def t_a_control_during_the_final_source_proof_cannot_restore_the_previous_actor():
    import driver_coach_service as service
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);f.command();f.step()
        for n in (2,3,4):f.complete(n);f.step()
        before=f.service.view(LOCAL);original=service._facts;changed=[]
        def facts(row):
            if not changed:changed.append(True);f.command('stop')
            return original(row)
        with patch.object(service,'_facts',facts):
            result=f.service.after_io(LOCAL,before,copy.deepcopy(f.snap))
        assert changed and not result['active'] and result['focus'] is None


def _records(record,count,first,step,start):
    """Finished copies of a real compact record with distinct ids and times."""
    rows=[]
    for n in range(count):
        row=copy.deepcopy(record);at=start+(first+n*step)*60
        row.update(id='%032x'%(first+n*step+1),training_id='%032x'%(first+n*step+1001),
                   started_at=at-50,updated_at=at,ended_at=at,status='stopped',reason='explicit_stop')
        rows.append(row)
    return rows


def t_merge_profile_histories_keeps_newest_32():
    now=time.time()
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root)
        store=history.Store(Path(root)/'solo',clock=lambda:now)
        local=_records(record,20,0,2,now-10_000)   # even slots
        remote=_records(record,20,1,2,now-10_000)  # odd slots
        for row in local:assert store.save(LOCAL,row)
        for row in remote:assert store.save(REMOTE,row)
        assert len(list(store.directory.glob('*.json')))==2
        moved=history.merge_profile_histories(Path(root)/'solo')
        expected=sorted(local+remote,key=lambda r:r['ended_at'],reverse=True)[:history.MAX_RECORDS]
        assert moved==sum(1 for r in expected if r in remote)==16,moved
        assert [p.name for p in store.directory.glob('*.json')]==[store._path(LOCAL).name],'The merged owner file stayed'
        raw=json.loads(store._path(LOCAL).read_text(encoding='utf8'))
        assert [r['id'] for r in raw['entries']]==[r['id'] for r in expected],'Merged history is not newest first'
        assert len(store.read(LOCAL,GENERATION)['entries'])==history.MAX_RECORDS
        assert history.merge_profile_histories(Path(root)/'solo')==0
        assert [r['id'] for r in json.loads(store._path(LOCAL).read_text(encoding='utf8'))['entries']]==[r['id'] for r in expected]


def t_merge_profile_histories_dedupes_by_id_and_keeps_unreadable_files():
    now=time.time()
    with tempfile.TemporaryDirectory() as root:
        _,record=sample(root)
        store=history.Store(Path(root)/'solo',clock=lambda:now)
        mine,shared,deleted,extra=_records(record,4,0,1,now-10_000)
        for row in (mine,shared,deleted):assert store.save(LOCAL,row)
        store.delete(LOCAL,store.read(LOCAL,GENERATION)['revision'],deleted['id'])
        newer=copy.deepcopy(shared);newer['updated_at']+=5
        for row in (copy.deepcopy(mine),newer,deleted,extra):assert store.save(REMOTE,row)
        broken=dict(LOCAL,subject='console:broken')
        assert store.save(broken,extra);store._path(broken).write_text('{broken',encoding='utf8')
        with patch.object(history.LOG,'warning') as warning:
            moved=history.merge_profile_histories(Path(root)/'solo')
        assert moved==1,moved
        entries=store.read(LOCAL,GENERATION)['entries']
        assert sorted(r['id'] for r in entries)==sorted([mine['id'],shared['id'],extra['id']]),'Duplicate or deleted entries came back'
        assert next(r for r in entries if r['id']==shared['id'])['updated_at']==newer['updated_at']
        assert not store._path(REMOTE).exists() and store._path(broken).exists(),'An unreadable file was removed'
        assert warning.call_count==1 and history.LOG.name.startswith('racecast')
        logged=' '.join(str(a) for call in warning.call_args_list for a in call.args)
        assert root not in logged and '.json' not in logged and 'coach-history' not in logged,logged
        assert history.merge_profile_histories(Path(root)/'missing')==0


def t_starting_the_coach_merges_the_profile_histories_once():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root);calls=[]
        with patch.object(history,'merge_profile_histories',lambda path:calls.append(path) or 0):
            f.service.start();f.service.start()
        f.service.close()
        assert calls==[store.profile_root],calls


def t_a_failing_history_merge_never_stops_the_coach_from_starting():
    with tempfile.TemporaryDirectory() as root:
        f,store=coordinated(root)
        def broken(_path):raise RuntimeError('unexpected merge failure in '+str(store.directory))
        with patch.object(history,'merge_profile_histories',broken),patch.object(history.LOG,'warning') as warning:
            try:f.service.start()
            except RuntimeError:pass  # Without the guard the failure escapes; the assertion below names it.
        try:
            assert f.service._thread is not None and f.service._thread.is_alive(),'A failing merge stopped the coach'
        finally:f.service.close()
        assert warning.call_count==1
        logged=' '.join(str(a) for call in warning.call_args_list for a in call.args)
        assert root not in logged and 'coach-history' not in logged,logged


if __name__=='__main__':
    tests=[(name,fn) for name,fn in globals().copy().items() if name.startswith('t_') and callable(fn)]
    for name,fn in tests:fn();print('PASS',name)
    print('PASS',len(tests),'private history scenarios')

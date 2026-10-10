#!/usr/bin/env python3
"""Validate facts/references, immutable history and untrusted report exports."""
import copy
import io
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import ai_reports as r
import ai_jobs
import ai_package
import gt7_context
import test_ai_package as fixtures
import test_ai_jobs as jobs


def result(package):
    lap=package['laps'][0];evidence=[dict(fact_id=lap['id']+'/time_s',value=lap['metrics']['time_s'])]
    reference=dict(lap_id=lap['id'],location_m=500,evidence=evidence)
    return dict(version=1,findings=[dict(priority=1,title='<script>alert(1)</script>',interpretation='Observed time; possible exit variation',possible_causes=['Unproven throttle timing'],**copy.deepcopy(reference))],
                exercises=[dict(priority=n,action='Practice exit '+str(n),check='Compare measured lap times',**copy.deepcopy(reference)) for n in (1,2,3)],limitations=['![untrusted](https://example.invalid/track)'])


def fail(fn,code='invalid_report'):
    try:fn()
    except r.ReportError as e:assert e.code==code,(e.code,code)
    except Exception as e:raise AssertionError('expected semantic '+code+', got '+type(e).__name__) from e
    else:raise AssertionError('expected '+code)


def setup(root,reference=False):
    root=Path(root);profile=root/'profile';s=fixtures.source(profile/'telemetry-recordings')
    other=fixtures.source(profile/'telemetry-recordings') if reference else None
    p=ai_package.build(s,1,template='consistency',language='de',references=[(other,1,2)] if other else [])
    pkg=root/'prepared';ai_package.write(p,pkg,'codex')
    adapter=jobs.FakeAdapter(root)
    adapter.script.write_text("import sys,json\nfrom pathlib import Path\nsys.stdin.read()\nPath(sys.argv[2]).write_text("+repr(json.dumps(result(p)))+")\nprint(json.dumps({'type':'turn.completed'}))\n")
    run=ai_jobs.Runner(root/'machine',profile,'profile').run(p,pkg,adapter,validate=r.validate)
    sources={ai_package.gt7_recording.recording_stem(candidate.name):candidate for candidate in (s,other) if candidate}
    def factory(name):
        candidate=sources[ai_package.gt7_recording.recording_stem(name)]
        return ai_package.load_source(profile/'telemetry-recordings',candidate.name,candidate.index)
    store=r.Store(profile,'profile',root/'machine',factory)
    return s,p,run,store


def t_schema_measurement_reference_and_priorities():
    with tempfile.TemporaryDirectory() as d:
        s=fixtures.source(Path(d)/'telemetry-recordings');p=ai_package.build(s,1);valid=result(p)
        report=r.validate(valid,p)
        assert report['findings'][0]['telemetry']['lap']==p['laps'][0]['lap']
        assert report['findings'][0]['evidence'][0]['provenance']
        assert len(report['exercises'])==3 and [x['priority'] for x in report['exercises']]==[1,2,3]
        variants=[]
        for field,value in [('lap_id','nonexistent'),('location_m',1001),('location_m',-1)]:
            bad=copy.deepcopy(valid);bad['findings'][0][field]=value;variants.append(bad)
        for evidence in ({'fact_id':'unknown','value':1},{'fact_id':p['laps'][0]['id']+'/time_s','value':999},{'fact_id':p['laps'][0]['id']+'/time_s','value':True}):
            bad=copy.deepcopy(valid);bad['findings'][0]['evidence']=[evidence];variants.append(bad)
        bad=copy.deepcopy(valid);bad['findings'][0].update(lap_id='unknown',evidence=[dict(fact_id='selection/mean_time_s',value=p['summary']['mean_time_s'])]);variants.append(bad)
        bad=copy.deepcopy(valid);bad['exercises']=bad['exercises'][:2];variants.append(bad)
        bad=copy.deepcopy(valid);bad['exercises'][1]['priority']=1;variants.append(bad)
        bad=copy.deepcopy(valid);bad['findings'][0]['measured_loss_s']=12;variants.append(bad)
        bad=copy.deepcopy(valid);bad['version']=True;variants.append(bad)
        bad=copy.deepcopy(valid);bad['findings'][0]['location_m']=float('nan');variants.append(bad)
        for bad in variants:fail(lambda bad=bad:r.validate(bad,p))
        comparable=copy.deepcopy(valid);comparable['findings'][0]['evidence'].append(dict(fact_id=p['laps'][1]['id']+'/time_s',value=p['laps'][1]['metrics']['time_s']))
        assert r.validate(comparable,p)['findings'][0]['evidence']
        unrelated=copy.deepcopy(p);unrelated['comparisons']=[]
        fail(lambda:r.validate(comparable,unrelated))
        assert valid['findings'][0]['evidence'][0].get('provenance') is None
        ambiguous=copy.deepcopy(p);duplicate=copy.deepcopy(p['laps'][0]);duplicate['metrics']['time_s']=999;ambiguous['references'].append(duplicate)
        ambiguous['facts'][duplicate['id']+'/time_s']['value']=999;claim=copy.deepcopy(valid)
        for item in claim['findings']+claim['exercises']:item['evidence'][0]['value']=999
        fail(lambda:r.validate(claim,ambiguous))


def t_comparable_lap_evidence_can_follow_the_shared_reference():
    with tempfile.TemporaryDirectory() as d:
        source=fixtures.source(Path(d)/'telemetry-recordings',[fixtures.row(2,time=101),fixtures.row(3,time=100),fixtures.row(4,time=102)])
        package=ai_package.build(source,1);output=result(package)
        output['findings'][0]['evidence'].append(dict(fact_id=package['laps'][2]['id']+'/time_s',value=102))
        assert r.validate(output,package)['findings'][0]['evidence'][-1]['value']==102
        package['comparisons']=[]
        fail(lambda:r.validate(output,package))


def t_history_provenance_exports_and_profile_scope():
    with tempfile.TemporaryDirectory() as d:
        s,p,run,store=setup(d)
        assert run['state']=='completed',run
        found=store.get(run['id']);assert not found['stale']
        assert found.get('input_file_fingerprints',{}).get('summary.md')==hashlib.sha256((Path(run['directory'])/'package/summary.md').read_bytes()).hexdigest()
        assert found['report']['language']=='de' and found['package_fingerprint']==p['fingerprint']
        assert store.history(s.name)['runs'][0]['id']==run['id']
        other=r.Store(Path(d)/'other','other',Path(d)/'machine')
        fail(lambda:other.get(run['id']),'not_found')
        destination=Path(d)/'other/telemetry-analyses'/p['selection']['recording_id']/run['id']
        shutil.copytree(run['directory'],destination)
        fail(lambda:other.get(run['id']),'not_found')
        fail(lambda:store.get('../outside'),'invalid_job')
        document=store.export(run['id'],'html',origin='http://127.0.0.1:8123')['bytes'].decode()
        assert 'href="http://127.0.0.1:8123/?' in document
        fail(lambda:store.export(run['id'],'html',origin='https://foreign.example.test'),'invalid_selection')
        assert '<script>alert(1)</script>' not in document and '&lt;script&gt;' in document
        assert 'Validated package facts' in document and 'Provenance' in document and 'unproven' in document
        assert 'requested_model' in document and 'time_s' in document and 'location_m=500' in document
        markdown=store.export(run['id'],'markdown')['bytes'].decode()
        assert '\\!\\[untrusted' in markdown and '## Limitations' in markdown and 'package_fingerprint' in markdown
        data=store.export(run['id'],'package')['bytes']
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert set(z.namelist())=={'detail.json','summary.md','manifest.json','result-schema.json'}
            assert json.loads(z.read('detail.json'))==p
        assert not any('executable' in a for a in found['agent'])
        directory=Path(run['directory']);prompt=directory/'package/summary.md';original_prompt=prompt.read_bytes();prompt.write_text('unselected changed prompt')
        fail(lambda:store.get(run['id']),'invalid_artifact')
        fail(lambda:store.export(run['id'],'package'),'invalid_artifact')
        prompt.write_bytes(original_prompt)
        directory=Path(run['directory']);(directory/'structured-output.json').write_text('{}')
        fail(lambda:store.get(run['id']))
        assert not store.history()['runs']


def t_relevant_staleness_does_not_rewrite_snapshots():
    with tempfile.TemporaryDirectory() as d:
        s,p,run,store=setup(d);directory=Path(run['directory'])
        original=(directory/'run.json').read_bytes();frozen=(directory/'package/detail.json').read_bytes()
        context=gt7_context.Store.for_recording(s.path);doc=context.read();data=doc['data']
        next(n for n in data['notes'] if n['id']=='other')['text']='Unselected note changed'
        context.save(data,expected=doc['revision'])
        assert not store.get(run['id'])['stale'], 'unrelated context revision marked the report stale'
        doc=context.read();data=doc['data'];next(n for n in data['notes'] if n['id']=='note')['text']='Selected goal changed'
        context.save(data,expected=doc['revision'])
        assert store.get(run['id'])['stale']
        assert (directory/'run.json').read_bytes()==original and (directory/'package/detail.json').read_bytes()==frozen
        Path(s.path).unlink()
        assert store.get(run['id'])['stale'] and store.export(run['id'],'html')['bytes']


def t_track_calculation_and_external_reference_changes_are_stale():
    with tempfile.TemporaryDirectory() as d:
        source,package,run,store=setup(d)
        source.index['laps'][0]['definition_fingerprint']='changed-definition'
        assert store.get(run['id'])['stale']
        source.index['laps'][0].pop('definition_fingerprint')
        assert not store.get(run['id'])['stale']
        original=ai_package.CALCULATION_VERSION
        try:
            ai_package.CALCULATION_VERSION='changed-calculation'
            assert store.get(run['id'])['stale']
        finally:ai_package.CALCULATION_VERSION=original
    with tempfile.TemporaryDirectory() as d:
        source,package,run,store=setup(d,reference=True)
        assert not store.get(run['id'])['stale']
        reference=Path(source.root)/(package['references'][0]['rec']+'.gt7rec')
        reference.write_bytes(reference.read_bytes()+b'changed')
        assert store.get(run['id'])['stale']
        assert store.export(run['id'],'package')['bytes']


def t_incomplete_and_cancelled_runs_never_export_reports():
    with tempfile.TemporaryDirectory() as d:
        s,p,run,store=setup(d);directory=Path(run['directory']);meta=json.loads((directory/'run.json').read_text())
        for state in ('cancelled','failed','timed_out','awaiting_validation','running'):
            doc=dict(meta,state=state,report=meta['report']);ai_jobs._save(directory/'run.json',doc)
            found=store.get(run['id']);assert found['report'] is None
            if state=='running':assert found['state']=='interrupted'
            fail(lambda:store.export(run['id'],'html'),'incomplete_report')
            assert store.export(run['id'],'package')['bytes']
        assert Path(d,'machine','ai-agents.json').exists() is False


def t_deleted_source_cannot_create_a_new_run_after_preview():
    with tempfile.TemporaryDirectory() as d:
        source,package,run,_store=setup(d)
        Path(source.path).unlink()
        runner=ai_jobs.Runner(Path(d)/'machine',Path(d)/'profile','profile')
        try:runner.run(package,Path(run['directory'])/'package',jobs.FakeAdapter(d),validate=r.validate)
        except ai_jobs.JobError as e:assert e.code=='stale_source'
        else:raise AssertionError('deleted source admitted a new orphan analysis')
        assert not runner.status('profile')['busy']


def t_deleted_or_changed_reference_cannot_start_after_preview():
    with tempfile.TemporaryDirectory() as d:
        profile=Path(d)/'profile';root=profile/'telemetry-recordings'
        own=fixtures.source(root);other=fixtures.source(root)
        assert own.name!=other.name
        package=ai_package.build(own,1,references=[(other,1,2)])
        original=Path(other.path).read_bytes();prepared=Path(d)/'prepared';ai_package.write(package,prepared,'codex')
        runner=ai_jobs.Runner(Path(d)/'machine',profile,'profile')
        for changed in (None,original+b'changed'):
            if changed is None:Path(other.path).unlink()
            else:Path(other.path).write_bytes(changed)
            try:runner.run(package,prepared,jobs.FakeAdapter(d),validate=r.validate)
            except ai_jobs.JobError as e:assert e.code=='stale_source'
            else:raise AssertionError('changed reference admitted an orphan analysis')
            assert not runner.status('profile')['busy']


def t_resolved_telemetry_lap_exposes_its_recording_identity():
    import test_racecast
    race=test_racecast.m
    with tempfile.TemporaryDirectory() as d:
        source=fixtures.source(Path(d)/'profile/telemetry-recordings')
        original={name:getattr(race,name) for name in ('_telemetry_rec_dir','_telemetry_full_index','_relay_open_file','_runtime_base_dir')}
        try:
            race._telemetry_rec_dir=lambda:source.root;race._telemetry_full_index=lambda path:source.index
            race._relay_open_file=lambda:None;race._runtime_base_dir=lambda:str(Path(d)/'machine')
            response=race.telemetry_lap_data(source.name,1,2)
            assert response['ok'] and response['lap'].get('recording_id')==source.identity
        finally:
            for name,value in original.items():setattr(race,name,value)


def t_recording_delete_removes_analysis_and_keeps_machine_settings():
    import test_racecast
    race=test_racecast.m
    with tempfile.TemporaryDirectory() as d:
        source,_package,run,store=setup(d)
        settings=Path(d)/'machine/ai-agents.json';settings.write_text('{"enabled":false}')
        original_base,original_open=race._runtime_base_dir,race._relay_open_file
        try:
            race._runtime_base_dir=lambda:str(Path(d)/'machine');race._relay_open_file=lambda:None
            error,notes=race._telemetry_delete_path(source.root,source.path)
        finally:race._runtime_base_dir,race._relay_open_file=original_base,original_open
        assert error is None and not notes
        assert not Path(source.path).exists() and not Path(run['directory']).exists()
        assert settings.read_text()=='{"enabled":false}' and not store.history()['runs']


def t_recording_delete_refuses_machine_job_admission():
    import test_racecast
    race=test_racecast.m
    with tempfile.TemporaryDirectory() as d:
        source,_package,run,_store=setup(d)
        original_base,original_open=race._runtime_base_dir,race._relay_open_file
        lease=ai_jobs.Lease(Path(d)/'machine').acquire()
        try:
            race._runtime_base_dir=lambda:str(Path(d)/'machine');race._relay_open_file=lambda:None
            error,_notes=race._telemetry_delete_path(source.root,source.path)
            assert error and 'analysis' in error.lower() and Path(source.path).exists()
            assert Path(run['directory']).exists()
        finally:
            lease.close();race._runtime_base_dir,race._relay_open_file=original_base,original_open


if __name__=='__main__':
    for name,fn in sorted(globals().copy().items()):
        if name.startswith('t_'):fn();print('PASS',name)

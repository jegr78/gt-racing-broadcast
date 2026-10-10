"""Deterministic validation, profile-owned history and safe report/input exports."""
import copy
import html
import io
import json
import math
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlencode

import ai_jobs
import ai_package


class ReportError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)


def _fail(message):raise ReportError('invalid_report',message)


def _schema(value,schema,depth=0):
    if depth>32:_fail('Result nesting exceeds the bound')
    expected=schema['type'];types=expected if isinstance(expected,list) else [expected]
    valid=any((t=='object' and isinstance(value,dict)) or (t=='array' and isinstance(value,list))
              or (t=='string' and isinstance(value,str)) or (t=='integer' and type(value) is int)
              or (t=='number' and type(value) in (int,float) and math.isfinite(value))
              or (t=='null' and value is None) for t in types)
    if not valid:_fail('Result field has an invalid type')
    if 'enum' in schema and value not in schema['enum']:_fail('Result field is outside the allowed values')
    if isinstance(value,str) and (not value.strip() or len(value)>8000 or any(ord(c)<32 and c not in '\n\t' for c in value)):
        _fail('Result text is empty, too long or contains control characters')
    if isinstance(value,dict):
        if set(value)!=set(schema['properties']):_fail('Result object has missing or extra fields')
        for k,v in value.items():_schema(v,schema['properties'][k],depth+1)
    if isinstance(value,list):
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',128):_fail('Result list has invalid length')
        for item in value:_schema(item,schema['items'],depth+1)


def _related(fact_id,lap_id,package):
    if fact_id.startswith('selection/'):return True
    if fact_id.startswith('comparison/'):
        try:comparison=package['comparisons'][int(fact_id.split('/')[1])]
        except (ValueError,IndexError,KeyError):return False
        return lap_id in (comparison['lap_id'],comparison['reference_id'])
    return fact_id.startswith(lap_id+'/')


def validate(result,package):
    """Validate measurements/references; narrative interpretations remain unproven."""
    _schema(result,ai_package.RESULT_SCHEMA)
    laps={r['id']:r for r in package['laps']+package['references']}
    if sorted(e['priority'] for e in result['exercises'])!=[1,2,3]:_fail('Exercises require distinct priorities 1, 2 and 3')
    report=copy.deepcopy(result)
    for item in report['findings']+report['exercises']:
        lap=laps.get(item['lap_id'])
        if lap is None:_fail('Result references a nonexistent selected lap')
        location=item['location_m'];end=lap['trace'][-1]['d']
        if location is not None and not 0<=location<=end:_fail('Result location leaves the selected lap trace')
        for evidence in item['evidence']:
            fact=package['facts'].get(evidence['fact_id'])
            if fact is None or evidence['value']!=fact['value'] or not _related(evidence['fact_id'],item['lap_id'],package):
                _fail('Result measurement does not match the selected package fact')
            evidence['provenance']=fact['provenance']
        item['telemetry']=dict(rec=lap['rec'],session=lap['session'],lap=lap['lap'],location_m=location,
                               recording_id=lap['recording_id'])
    report['findings'].sort(key=lambda f:f['priority']);report['exercises'].sort(key=lambda e:e['priority'])
    report.update(format='racecast-ai-report',language=package['language'],package_fingerprint=package['fingerprint'])
    report['limitations']=list(dict.fromkeys(package['limitations']+report['limitations']))
    return report


def _relevance(package):
    doc=copy.deepcopy(package);doc.pop('fingerprint',None)
    def prune(value):
        if isinstance(value,dict):
            if value.get('snapshot_kind')=='selected-effective-context-v1':value.pop('revision',None)
            for nested in value.values():prune(nested)
        elif isinstance(value,list):
            for nested in value:prune(nested)
    prune(doc);return ai_package.fingerprint(doc)


def stale(package,source_factory):
    try:
        selection=package['selection'];source=source_factory(selection['recording'])
        refs=[(source_factory(r['rec']),r['session'],r['lap']) for r in package['references']]
        current=ai_package.build(source,selection['session'],laps=selection['laps'],references=refs,
            template=package['template'],goal=package['goal'],questions=package['questions'],language=package['language'])
        return dict(stale=_relevance(package)!=_relevance(current),reason='Relevant source, context or reference definitions changed' if _relevance(package)!=_relevance(current) else None)
    except (ValueError,OSError,KeyError,ai_package.gt7_recording.RecordingError):
        return dict(stale=True,reason='Current source/context/reference selection is unavailable; the historical snapshot is retained')


def _nonfinite(value):raise ValueError('Non-finite artifact JSON')


def _read(path,root):
    path=Path(path)
    if not path.resolve().is_relative_to(Path(root).resolve()):raise ReportError('invalid_artifact','Analysis artifact leaves the active profile')
    try:return json.loads(ai_jobs.regular_output_bytes(path),parse_constant=_nonfinite)
    except (ValueError,OSError,ai_jobs.JobError) as e:raise ReportError('invalid_artifact','Analysis artifact is not valid bounded local JSON') from e


def read_package(directory,root):
    package=_read(Path(directory)/'package/detail.json',root)
    if not isinstance(package,dict):raise ReportError('invalid_artifact','Input package is not an object')
    expected=copy.deepcopy(package);digest=expected.pop('fingerprint',None)
    if ai_package.fingerprint(expected)!=digest:raise ReportError('invalid_artifact','Saved input package fingerprint changed')
    return package


class Store:
    def __init__(self,profile_root,profile,machine,source_factory=None):
        self.root=Path(profile_root).resolve();self.profile=profile;self.machine=Path(machine).resolve()
        self.base=self.root/'telemetry-analyses';self.source_factory=source_factory
    def directory(self,job_id):
        if not isinstance(job_id,str) or not re.fullmatch('[0-9a-f]{32}',job_id):raise ReportError('invalid_job','Invalid analysis job ID')
        for path in self.base.glob('*/'+job_id):
            if not re.fullmatch('[0-9a-f]{32}|[0-9a-f]{64}',path.parent.name):continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):continue
            doc=_read(path/'run.json',self.root)
            if isinstance(doc,dict) and doc.get('id')==job_id and doc.get('profile')==self.profile:return path
        raise ReportError('not_found','Analysis run not found in the active profile')
    def get(self,job_id,check_stale=True):
        directory=self.directory(job_id);run=_read(directory/'run.json',self.root)
        if run['state']=='completed':
            package=read_package(directory,self.root)
            checked=validate(_read(directory/'structured-output.json',self.root),package)
            if checked!=run.get('report'):raise ReportError('invalid_artifact','Stored report differs from validated output')
            run.update(stale(package,self.source_factory) if check_stale and self.source_factory else dict(stale=False,reason=None))
        else:
            run.update(stale=False,reason=None)
            if run['state'] not in ai_jobs.TERMINAL:
                status=ai_jobs.Runner(self.machine,self.root,self.profile).status(self.profile)
                if not status['busy'] or not status['active'] or status['active'].get('id')!=job_id:
                    run.update(state='interrupted',progress='Process ended without a completed report; retained artifacts are incomplete')
        return dict(run,ok=True)
    def history(self,rec=None):
        items=[]
        for path in self.base.glob('*/*/run.json'):
            try:
                run=self.get(path.parent.name)
                if rec is not None and ai_package.gt7_recording.recording_stem(run['selection']['recording'])!=ai_package.gt7_recording.recording_stem(rec):continue
                run.pop('report',None);items.append(run)
            except (ReportError,KeyError,TypeError):continue
        items.sort(key=lambda r:r['created_at'],reverse=True)
        return dict(ok=True,profile=self.profile,runs=items[:200])
    def export(self,job_id,kind):
        directory=self.directory(job_id)
        if kind=='package':
            read_package(directory,self.root)
            files={name:ai_jobs.regular_output_bytes(directory/'package'/name) for name in ('detail.json','summary.md','manifest.json','result-schema.json')}
            return dict(filename='racecast-input-'+job_id+'.zip',mime='application/zip',bytes=_archive(files))
        run=self.get(job_id,check_stale=False)
        if kind not in ('html','markdown'):raise ReportError('invalid_selection','Choose HTML, Markdown or the input package')
        if run['state']!='completed':raise ReportError('incomplete_report','Only validated completed reports can be exported as reports')
        package=read_package(directory,self.root)
        rendered=render(run,package,kind)
        return dict(filename='racecast-analysis-'+job_id+('.html' if kind=='html' else '.md'),
                    mime='text/html; charset=utf-8' if kind=='html' else 'text/markdown; charset=utf-8',bytes=rendered.encode('utf-8'))


def _archive(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,raw in files.items():z.writestr(name,raw)
    return out.getvalue()


def export_package(package,provider):
    with tempfile.TemporaryDirectory(prefix='racecast-ai-export-') as root:
        path=Path(root)/'package';ai_package.write(package,path,provider)
        return _archive({name:(path/name).read_bytes() for name in ('detail.json','summary.md','manifest.json','result-schema.json')})


def _markdown(value):
    return re.sub(r'([\\`*_{}\[\]()<>#+!|])',r'\\\1',str(value))


def render(run,package,kind):
    report=run['report'];sections=[]
    for label,rows in [('Findings',report['findings']),('Exercises',report['exercises'])]:
        sections.append((label,rows))
    provenance={k:run.get(k) for k in ('id','profile','selection','package_fingerprint','agent','agent_fingerprint','provider_version','requested_model','actual_model','created_at','started_at','finished_at','state')}
    provenance['input']={k:package[k] for k in ('version','schema_version','calculation_version','source','context','reference_provenance','track_definition_snapshots','shift_reference_snapshots','template','template_version','goal','questions','language')}
    if kind=='markdown':
        out=['# Racecast analysis','', 'Language: '+_markdown(report['language']), '', 'Measurements below were validated against the immutable package. Interpretations and possible causes remain unproven.', '']
        for label,rows in sections:
            out+=['## '+label,'']
            for item in rows:
                out+=['### Priority '+str(item['priority'])+': '+_markdown(item.get('title',item.get('action'))),'']
                for field in ('interpretation','check'):
                    if field in item:out+=[_markdown(item[field]),'']
                if item.get('possible_causes'):out+=['Possible causes: '+_markdown('; '.join(item['possible_causes'])),'']
                q=urlencode({k:v for k,v in item['telemetry'].items() if k!='recording_id' and v is not None})
                out+=['[Telemetry reference](/?'+q+'#telemetry)','', 'Measured evidence:','']
                out += ['- '+_markdown(e['fact_id'])+': '+_markdown(e['value'])+' ('+_markdown(e['provenance'])+')' for e in item['evidence']]
                out+=['']
        out+=['## Limitations','']+['- '+_markdown(v) for v in report['limitations']]+['','## Provenance','','```json',json.dumps(provenance,indent=2,ensure_ascii=False),'```','']
        return '\n'.join(out)
    esc=lambda v:html.escape(str(v),quote=True)
    out=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Racecast analysis</title><style>body{max-width:960px;margin:2rem auto;padding:1rem;font:16px system-ui;line-height:1.5}pre{white-space:pre-wrap;overflow-wrap:anywhere}li{margin:.4rem 0}article{border-top:1px solid #aaa;padding-top:1rem}</style><body><h1>Racecast analysis</h1><p>Language: '+esc(report['language'])+'</p><p>Measurements were validated against the immutable package. Interpretations and possible causes remain unproven.</p>']
    for label,rows in sections:
        out+=['<h2>'+label+'</h2>']
        for item in rows:
            out+=['<article><h3>Priority '+str(item['priority'])+': '+esc(item.get('title',item.get('action')))+'</h3>']
            for field in ('interpretation','check'):
                if field in item:out+=['<p>'+esc(item[field])+'</p>']
            if item.get('possible_causes'):out+=['<p>Possible causes: '+esc('; '.join(item['possible_causes']))+'</p>']
            q=urlencode({k:v for k,v in item['telemetry'].items() if k!='recording_id' and v is not None})
            out+=['<a href="'+esc('/?'+q+'#telemetry')+'">Telemetry reference</a><h4>Measured evidence</h4><ul>']
            out+=['<li>'+esc(e['fact_id'])+': '+esc(e['value'])+' ('+esc(e['provenance'])+')</li>' for e in item['evidence']]
            out+=['</ul></article>']
    out+=['<h2>Limitations</h2><ul>']+['<li>'+esc(v)+'</li>' for v in report['limitations']]+['</ul><h2>Provenance</h2><pre>'+esc(json.dumps(provenance,indent=2,ensure_ascii=False))+'</pre></body></html>']
    return '\n'.join(out)


def delete_recording_analyses(profile_root,recording_id,recording_name=None):
    """Remove profile-owned runs by source identity or its immutable recording name."""
    root=Path(profile_root).resolve();base=root/'telemetry-analyses'
    if base.is_symlink() or not base.resolve().is_relative_to(root):
        raise ReportError('invalid_artifact','Refusing redirected analysis deletion')
    identities=set()
    if recording_id is not None:
        if not isinstance(recording_id,str) or not re.fullmatch('[0-9a-f]{32}|[0-9a-f]{64}',recording_id):
            raise ReportError('invalid_artifact','Cannot identify recording-owned analysis artifacts')
        identities.add(recording_id)
    if recording_name:
        for path in base.glob('*/*/run.json'):
            try:run=_read(path,root)
            except ReportError:continue
            if (isinstance(run,dict) and run.get('format')=='racecast-ai-run'
                    and (run.get('selection') or {}).get('recording')==recording_name):
                ident=path.parent.parent.name
                if re.fullmatch('[0-9a-f]{32}|[0-9a-f]{64}',ident):identities.add(ident)
    for ident in identities:
        directory=base/ident
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            raise ReportError('invalid_artifact','Refusing redirected analysis deletion')
        if directory.exists():shutil.rmtree(directory)

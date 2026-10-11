#!/usr/bin/env python3
"""Preview confirmation and binding across API and CLI-facing analysis control."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import ai_control as c
import ai_agents
import test_ai_package as fixtures
import test_ai_jobs as job_fixtures


def setup(d):
    root=Path(d);profile=root/'profile';source=fixtures.source(profile/'telemetry-recordings')
    store=ai_agents.Settings(root/'machine')
    store.save(dict(enabled=True,agents=[dict(id='coach',name='Coach',provider='codex',model='exact')]))
    adapters=[]
    def adapter(cfg):
        a=job_fixtures.FakeAdapter(root);a.config=ai_agents.agent_config(cfg);adapters.append(a);return a
    control=c.Controller(root/'machine',profile,'profile',lambda name:source,adapter_factory=adapter)
    return control,store,source,adapters


def failure(fn,code):
    try:fn()
    except c.ControlError as e:assert e.code==code,(e.code,code)
    else:raise AssertionError('expected '+code)


def t_preview_and_explicit_confirmed_start():
    with tempfile.TemporaryDirectory() as d:
        control,store,source,adapters=setup(d)
        payload=dict(profile='profile',rec=source.name,session=1,agent='coach',template='consistency')
        preview=control.preview(payload)
        assert preview['can_start'] and preview['confirm_preview']
        assert preview['manifest']['transmission'] and all(a.calls==0 for a in adapters)
        failure(lambda:control.start(payload),'preview_required')
        failure(lambda:control.start(dict(payload,confirm_preview='0'*64),background=False),'preview_changed')
        started=control.start(dict(payload,confirm_preview=preview['confirm_preview']),background=False)
        assert started['state']=='awaiting_validation' and adapters[-1].calls==1
        assert control.history(source.name)['runs'][0]['state']=='awaiting_validation'
        exported=control.export(started['id'],'package')
        assert exported['encoding']=='base64' and exported['mime']=='application/zip'
        assert control.export_preview(dict(payload,confirm_preview=preview['confirm_preview']))['content']
        assert control.job(started['id'])['profile']=='profile'
        other=c.Controller(control.machine,Path(d)/'other','other',lambda name:source)
        failure(lambda:other.job(started['id']),'not_found')
        altered=dict(payload,goal='Changed goal',confirm_preview=preview['confirm_preview'])
        failure(lambda:control.start(altered),'preview_changed')
        settings=store.read();settings['agents'][0]['model']='different';store.save(settings)
        failure(lambda:control.start(dict(payload,confirm_preview=preview['confirm_preview'])),'preview_changed')


def t_disabled_and_profile_selection_fail_before_execution():
    with tempfile.TemporaryDirectory() as d:
        control,store,source,adapters=setup(d)
        payload=dict(rec=source.name,session=1,agent='coach',profile='foreign')
        failure(lambda:control.preview(payload),'profile_changed')
        payload['profile']='profile';settings=store.read();settings['enabled']=False;store.save(settings)
        failure(lambda:control.preview(payload),'disabled')
        assert not adapters
        settings['enabled']=True;store.save(settings)
        foreign=c.Controller(control.machine,Path(d)/'other','profile',lambda name:source,adapter_factory=control.adapter_factory)
        failure(lambda:foreign.preview(payload),'invalid_source')
        assert not adapters
        failure(lambda:control.preview(dict(payload,command='anything')),'invalid_selection')


def t_unverified_isolation_has_manual_guidance_and_exportable_preview():
    with tempfile.TemporaryDirectory() as d:
        control,store,source,_adapters=setup(d)
        settings=store.read();settings['agents'][0].update(mode='personal',personal={'mcp':True});store.save(settings)
        control.adapter_factory=ai_agents.Adapter
        preview=control.preview(dict(rec=source.name,session=1,agent='coach'))
        assert preview['can_start'] is False and preview['availability']['guidance']
        assert preview['manifest']['laps']


def t_completed_selection_and_explicit_suggestions_are_profile_bound():
    with tempfile.TemporaryDirectory() as d:
        control,store,source,adapters=setup(d)
        selected=control.selection(source.name)
        assert selected['profile']=='profile' and selected['rec']==source.name
        assert [s['session'] for s in selected['sessions']]==[1]
        assert selected['sessions'][0]['laps'] and all(l['lap']>0 for l in selected['sessions'][0]['laps'])
        refs=control.references(source.name,'1')
        assert refs['suggestions'] and all(set(r)=={'rec','session','lap','time_s','kind'} for r in refs['suggestions'])
        assert all(r['session']==1 for r in refs['suggestions']) and not adapters
        failure(lambda:control.references(source.name,'invalid'),'invalid_selection')
        failure(lambda:control.references(source.name,'2'),'incomplete_session')
        failure(lambda:control.references(source.name,True),'invalid_selection')
        foreign=c.Controller(control.machine,Path(d)/'other','other',lambda name:source)
        failure(lambda:foreign.selection(source.name),'invalid_source')
        failure(lambda:foreign.references(source.name,'1'),'invalid_source')
        document=store.read();document['enabled']=False;store.save(document)
        failure(lambda:control.selection(source.name),'disabled')
        failure(lambda:control.references(source.name,'1'),'disabled')


def t_preview_discloses_agent_mode_and_exact_model_without_local_paths():
    with tempfile.TemporaryDirectory() as d:
        control,store,source,_=setup(d)
        preview=control.preview(dict(rec=source.name,session=1,agent='coach',model='manual-model'))
        assert preview['requested_model']=='manual-model'
        assert preview['agent']==dict(id='coach',name='Coach',provider='codex',mode='isolated',personal={'instructions':False,'skills':False,'mcp':False})
        assert 'executable' not in preview['agent']


if __name__=='__main__':
    for n,f in sorted(globals().copy().items()):
        if n.startswith('t_'):f();print('PASS',n)

#!/usr/bin/env python3
"""Maintainer subscription integration check. Uses synthetic telemetry, never CI credentials."""
import argparse
import json
import pathlib
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',choices=('codex','claude'),required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--executable',default='')
    parser.add_argument('--timeout',type=int,default=120)
    args=parser.parse_args()
    repo=pathlib.Path(__file__).resolve().parents[1]
    sys.path[:0]=[str(repo/'tests'),str(repo/'src/scripts')]
    import logsetup
    logsetup.harden_stdio()
    import test_ai_package as fixture
    import ai_agents
    import ai_control
    import ai_package
    import ai_reports
    root=pathlib.Path(tempfile.mkdtemp(prefix='racecast-native-integration-')).resolve()
    profile=root/'profile';source=fixture.source(profile/'telemetry-recordings')
    config=dict(id='release',name='Synthetic subscription check',provider=args.provider,model=args.model,executable=args.executable,timeout=args.timeout)
    settings=ai_agents.Settings(root/'machine');settings.save(dict(enabled=True,agents=[config]))
    control=ai_control.Controller(root/'machine',profile,'profile',lambda name:ai_package.load_source(source.root,source.name,source.index),validate=ai_reports.validate)
    payload=dict(rec=source.name,session=1,agent='release',template='consistency',language='en')
    preview=control.preview(payload)
    if not preview['can_start']:
        print(json.dumps(preview['availability'],indent=2));return 1
    print('Synthetic artifacts:',root,flush=True)
    run=control.start(dict(payload,confirm_preview=preview['confirm_preview']),background=False)
    proof={k:run.get(k) for k in ('state','provider_version','requested_model','actual_model','error')}
    if run['state']!='completed':
        print(json.dumps(proof,indent=2));return 1
    report=control.job(run['id']);assert len(report['report']['exercises'])==3
    assert control.export(run['id'],'html')['content'] and control.export(run['id'],'package')['content']
    package=ai_package.build(source,1);prepared=root/'prepared';ai_package.write(package,prepared,args.provider)
    runner=control.runner
    failed=runner.run(package,prepared,ai_agents.Adapter(dict(config,model='racecast-no-such-model')),validate=ai_reports.validate)
    assert failed['state']=='failed' and failed['report'] is None
    proof['failure']=failed.get('error')
    job=runner.start(package,prepared,ai_agents.Adapter(config),validate=ai_reports.validate)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        status=runner.status('profile')
        if status['active'] and status['active']['state']=='running':break
        time.sleep(.05)
    assert runner.cancel(job)
    while time.monotonic()<deadline and runner.status('profile')['busy']:time.sleep(.05)
    cancelled=json.loads(next((profile/'telemetry-analyses').glob('*/'+job+'/run.json')).read_text())
    assert cancelled['state']=='cancelled' and cancelled['report'] is None and not runner.status('profile')['busy']
    proof['cancellation']='cancelled; no report; machine lease released'
    (root/'proof.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps(proof,indent=2));return 0


if __name__=='__main__':sys.exit(main())

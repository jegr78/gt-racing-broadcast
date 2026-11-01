#!/usr/bin/env python3
"""Operator controls use the existing jobs, confirmations and output console."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/scripts'),str(ROOT/'src/ui')]
import ui_ops
import ui_jobs


def rejected(fn):
    try:fn()
    except ValueError:return
    except SystemExit as exc:raise AssertionError('Invalid action input reached process exit') from exc
    raise AssertionError('Invalid action input accepted')


def t_new_jobs_build_exact_bounded_operator_arguments():
    cases=[('obs-benchmark',{'window':'30','settle':'3'},['obs','benchmark','--window','30','--settle','3']),
           ('chat-pull',{'ip':'100.64.0.2','port':'8088'},['chat','pull','100.64.0.2','--port','8088']),
           ('health-pull',{'ip':'producer.example.ts.net'},['health','pull','producer.example.ts.net']),
           ('chat-import',{'file':'saved chat.json'},['chat','import','saved chat.json']),
           ('chat-export',{'out':'chat export.json'},['chat','export','--out','chat export.json']),
           ('console-pull-versions',{'ip':'100.64.0.2'},['console','pull-versions','100.64.0.2']),
           ('console-setup-funnel',{},['console','setup-funnel']),
           ('console-apply-funnel',{},['console','setup-funnel','--apply']),
           ('streams-restart',{},['streams','restart']),
           ('companion-open-admin',{},['companion','open-admin']),
           ('companion-enable-control',{},['companion','enable-control']),
           ('obs-browser',{},['obs-browser','--yes']),
           ('smoketest',{'minutes':'2'},['smoketest','--minutes','2'])]
    for name,params,expected in cases:
        assert ui_ops.build_argv(name,params)==expected,(name,params)
    for verb in ('start','stop','status'):
        assert ui_ops.build_argv('telemetry-record-'+verb)==['telemetry','record',verb]


def t_new_jobs_reject_options_control_characters_and_unbounded_values():
    for name,params in [('chat-pull',{}),('chat-import',{}),
                        ('health-pull',{'ip':'--force'}),('chat-pull',{'ip':'host/other'}),
                        ('chat-pull',{'ip':'100.64.0.2','port':0}),
                        ('chat-pull',{'ip':'100.64.0.2','port':65536}),
                        ('chat-import',{'file':'--force'}),('chat-export',{'out':'file\nother'}),
                        ('obs-benchmark',{'window':True}),('obs-benchmark',{'window':'30.5'}),
                        ('obs-benchmark',{'settle':'2'}),('obs-benchmark',{'window':601}),
                        ('telemetry-record-start',{'force':True})]:
        rejected(lambda name=name,params=params:ui_ops.build_argv(name,params))


def t_live_http_jobs_freeze_profile_and_reject_bad_or_stale_requests():
    import test_ui_server as fixture
    calls=[]
    class Jobs:
        def start(self,name,argv):calls.append((name,argv));return 'synthetic',None
    ctx=fixture._ctx();ctx.update(ops=ui_ops.OPS,build_argv=ui_ops.build_argv,
        profile_ops=ui_ops.PROFILE_OPS,active_profile=lambda:'demo',jobs=Jobs())
    server,port=fixture._serve(ctx)
    try:
        status,raw=fixture._post_json(port,'/api/op/chat-import',{'profile':'demo','params':{'file':'saved chat.json'}})
        assert status==200 and json.loads(raw)=={'ok':True,'job_id':'synthetic'},raw
        assert calls==[('chat-import',['--profile','demo','chat','import','saved chat.json'])]
        for body,code,part in (({'profile':'other','params':{'file':'saved.json'}},409,'Active league changed'),
                               ({'profile':'demo','params':{'file':'--force'}},400,'invalid file path'),
                               ({'profile':'demo','params':{'file':'saved.json','force':True}},400,'unexpected parameter')):
            status,raw=fixture._post_json(port,'/api/op/chat-import',body);result=json.loads(raw)
            assert status==code and result['ok'] is False and part in result['error'],(status,result)
            assert len(calls)==1
    finally:server.shutdown();server.server_close()


def t_opaque_browser_origin_cannot_launch_operator_jobs_with_simple_json_post():
    import http.client
    import test_ui_server as fixture
    calls=[]
    class Jobs:
        def start(self,name,argv,**kwargs):calls.append(('job',name,argv));return 'synthetic',None
    ctx=fixture._ctx();ctx.update(ops=ui_ops.OPS,build_argv=ui_ops.build_argv,jobs=Jobs(),
        profile_ops=ui_ops.PROFILE_OPS,active_profile=lambda:'demo')
    server,port=fixture._serve(ctx)
    try:
        for path,body in [('/api/op/smoketest',{'profile':'demo','confirmation':'CLEAR SCHEDULE demo','params':{}}),
                          ('/api/op/console-apply-funnel',{'params':{}})]:
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            try:
                conn.request('POST',path,body=json.dumps(body),
                    headers={'Origin':'null','Content-Type':'text/plain'})
                response=conn.getresponse();result=json.loads(response.read())
                assert response.status==403 and result=={'ok':False,'error':'cross-origin request blocked'},(response.status,result)
                assert calls==[],calls
            finally:conn.close()
    finally:server.shutdown();server.server_close()


def t_smoke_http_requires_typed_confirmation_and_freezes_profile_before_job():
    import test_ui_server as fixture
    calls=[];active={'name':'demo'}
    class Jobs:
        def start(self,name,argv,**kwargs):calls.append((name,argv,kwargs));return 'synthetic',None
    ctx=fixture._ctx();ctx.update(ops=ui_ops.OPS,build_argv=ui_ops.build_argv,
        profile_ops=ui_ops.PROFILE_OPS,active_profile=lambda:active['name'],jobs=Jobs())
    server,port=fixture._serve(ctx)
    try:
        for typed in (None,True,'','CLEAR SCHEDULE other','CLEAR SCHEDULE demo\nextra',' '*513+'CLEAR SCHEDULE demo'):
            status,raw=fixture._post_json(port,'/api/op/smoketest',{'profile':'demo','params':{'minutes':2},'confirmation':typed})
            result=json.loads(raw)
            assert status==400 and not result['ok'] and 'Type CLEAR SCHEDULE demo exactly' in result['error'],(status,result)
            assert calls==[]
        status,raw=fixture._post_json(port,'/api/op/smoketest',{'profile':'demo','params':{'minutes':2},'confirmation':'CLEAR SCHEDULE demo'})
        assert status==200 and json.loads(raw)['ok'],raw
        assert calls==[('smoketest',['--profile','demo','smoketest','--minutes','2'],{'input_text':'CLEAR SCHEDULE demo\n'})],calls
        active['name']='other'
        status,raw=fixture._post_json(port,'/api/op/smoketest',{'profile':'demo','params':{},'confirmation':'CLEAR SCHEDULE demo'})
        result=json.loads(raw)
        assert status==409 and not result['ok'] and 'Active league changed' in result['error'] and len(calls)==1
    finally:server.shutdown();server.server_close()


def t_job_passes_only_the_typed_input_to_the_actual_cli_confirmation():
    # This child invokes only the pure confirmation guard, never the smoke run.
    code="import sys;sys.path.insert(0,sys.argv[1]);import racecast;racecast._smoke_confirm('demo',[]);print('guard accepted')"
    jobs=ui_jobs.JobManager(lambda args:[sys.executable,'-c',code,str(ROOT/'src')])
    for typed,expected in [('CLEAR SCHEDULE demo\n',0),('wrong phrase\n',1)]:
        job,error=jobs.start('smoketest',['--profile','demo','smoketest'],input_text=typed)
        assert not error,error
        deadline=time.monotonic()+10
        while jobs.snapshot(job)['exit_code'] is None and time.monotonic()<deadline:time.sleep(.02)
        state=jobs.snapshot(job);lines=jobs.lines_since(job,0)[0]
        assert state['exit_code']==expected,(state,lines)
        assert any('CLEAR SCHEDULE demo' in line for line in lines),lines
        assert any('guard accepted' in line for line in lines)==(expected==0),lines


def t_confirmation_input_is_finite_and_reserved_for_smoketest():
    calls=[]
    jobs=ui_jobs.JobManager(list,spawn=lambda *args,**kwargs:calls.append((args,kwargs)))
    for op,value in [('other','CLEAR SCHEDULE demo\n'),('smoketest',True),('smoketest','x'*513)]:
        rejected(lambda op=op,value=value:jobs.start(op,[],input_text=value))
    assert calls==[]


def t_smoke_job_cannot_be_force_cancelled_before_its_existing_cli_cleanup():
    import io
    class Process:
        def __init__(self):self.stdout=io.BytesIO(b'');self.returncode=None
        def wait(self):time.sleep(.2);self.returncode=0;return 0
        def terminate(self):raise AssertionError('Smoke cleanup cannot be skipped by terminate')
    jobs=ui_jobs.JobManager(list,spawn=lambda argv,**kwargs:Process())
    job,error=jobs.start('smoketest',['--profile','demo','smoketest'],input_text='CLEAR SCHEDULE demo\n')
    assert not error and jobs.cancel(job) is False and not jobs.snapshot(job)['cancelled']
    deadline=time.monotonic()+5
    while jobs.snapshot(job)['exit_code'] is None and time.monotonic()<deadline:time.sleep(.02)
    assert jobs.snapshot(job)['exit_code']==0


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

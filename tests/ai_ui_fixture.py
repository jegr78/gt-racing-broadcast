"""Synthetic Control Center with real AI controller/store/process callbacks."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src/scripts'),str(ROOT/'src/ui')]
import ai_agents
import ai_control
import ai_package
import ai_reports
import test_ai_jobs as job_fixture
import test_ai_package as package_fixture
import test_ui_server as web_fixture


class Fixture:
    def __init__(self,root):
        self.root=Path(root);self.profile='profile';self.delay=.5;self.invalid=False;self.title='<script>window.aiInjected=true</script>'
        self.source=package_fixture.source(self.root/'profile/telemetry-recordings')
        self.sources={'profile':self.source}
        self.settings=ai_agents.Settings(self.root/'machine')
        self.settings.save(dict(enabled=True,agents=[dict(id='coach',name='Synthetic coach',provider='codex',model='exact')]))

    def adapter(self,cfg):
        adapter=job_fixture.FakeAdapter(self.root);adapter.config=ai_agents.agent_config(cfg)
        code="""import sys,json,time
from pathlib import Path
sys.stdin.read();p=json.loads(Path('detail.json').read_text());lap=p['laps'][0]
e=[{'fact_id':lap['id']+'/time_s','value':lap['metrics']['time_s']}];ref={'lap_id':lap['id'],'location_m':500,'evidence':e}
result={'version':1,'findings':[dict(priority=1,title='<script>window.aiInjected=true</script>',interpretation='Observed lap time; exit variation is a hypothesis.',possible_causes=['Unproven throttle timing'],**ref)],'exercises':[dict(priority=n,action='Practice smooth exits '+str(n),check='Compare supplied lap times',**ref) for n in (1,2,3)],'limitations':['Synthetic telemetry and controlled CLI; no provider account used.']}
"""
        code=code.replace(repr('<script>window.aiInjected=true</script>'),repr(self.title))
        code+='time.sleep('+repr(self.delay)+')\n'
        code+='Path(sys.argv[2]).write_text(json.dumps('+('{}' if self.invalid else 'result')+"));print(json.dumps({'type':'turn.completed'}))\n"
        adapter.script.write_text(code);return adapter

    def control(self):
        root=self.root/self.profile;current=self.sources.get(self.profile,self.source)
        def source(name):
            path=name if name.endswith('.gt7rec') else name+'.gt7rec'
            return ai_package.load_source(root/'telemetry-recordings',path,current.index)
        return ai_control.Controller(self.root/'machine',root,self.profile,source,adapter_factory=self.adapter,validate=ai_reports.validate)

    def ai(self,operation,payload):
        try:
            control=self.control()
            if operation not in ('settings','settings-save','probe') and payload.get('profile',self.profile)!=self.profile:
                raise ai_control.ControlError('profile_changed','Active profile changed; reload explicitly')
            if operation=='settings':return dict(self.settings.read(),ok=True)
            if operation=='settings-save':return dict(self.settings.save(payload),ok=True)
            if operation=='probe':return dict(ok=True,status='ready',version='fixture',model_suggestions=['exact'],guidance='Controlled CLI fixture, no subscription invocation.')
            if operation=='selection':return control.selection(payload.get('rec'))
            if operation=='references':return control.references(payload.get('rec'),payload.get('session'))
            if operation=='preview':return control.preview(payload)
            if operation=='start':return control.start(payload)
            if operation=='cancel':return control.cancel(payload.get('id'))
            if operation=='status':return control.status()
            if operation=='history':return control.history(payload.get('rec'))
            if operation=='job':return control.job(payload.get('id'))
            if operation=='export':return control.export(payload.get('id'),payload.get('format'),payload.get('origin'))
            if operation=='package-export':return control.export_preview(payload)
            raise ai_control.ControlError('invalid_selection','Unknown operation')
        except (ai_control.ControlError,ai_agents.AgentError) as exc:return dict(ok=False,error=dict(code=exc.code,message=str(exc)))

    def context(self):
        ctx=web_fixture._ctx();ctx['version']='dev'
        ctx['status']=lambda:{'relay':{'alive':False},'version':'dev','streams':[],'companion':{'running':False}}
        ctx['update_check']=lambda force=False:dict(ok=True,current='dev',latest=None,update_available=False,note='development build')
        ctx['profiles']=lambda:dict(ok=True,active=self.profile,profiles=[dict(name=n,kind='solo',template='pov') for n in ('profile','other')])
        def use(name):self.profile=name;return ctx['profiles']()
        ctx['profile_use']=use
        ctx['profile_env_read']=lambda:dict(ok=True,entries=[])
        ctx['ai_request']=self.ai
        rec=self.source.index['rec'];rows=self.source.index['laps'];track={'track':'Fixture track','layout':'Forward'}
        recording=dict(rec=rec,name=self.source.name,indexed=True,recording=False,laps=2,completed_laps=2,partial_segments=0,track=track)
        ctx['telemetry_recordings']=lambda:dict(ok=True,recordings=[recording] if self.profile=='profile' else [])
        ctx['telemetry_laps']=lambda *a,**kw:dict(ok=True,recording=recording,laps=rows,best_sectors=[],theoretical_best=None,comparison_warnings=[],unindexed=0,sector_m=500)
        ctx['telemetry_lap']=lambda name,session,lap,**kw:dict(ok=True,lap=dict(next(r for r in rows if r['session']==int(session) and r['lap']==int(lap)),recording_id=self.source.identity),sector_m=500)
        return ctx

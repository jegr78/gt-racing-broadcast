#!/usr/bin/env python3
"""Real signed radio pages and loopback-only producer controls with synthetic state."""
import copy,http.client,json,sys,tempfile,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src/scripts'),str(Path(__file__).parent)]
from test_driver_radio_service import Fixture
from test_driver_radio_native import Worker,Player
import test_console_gate as console
import test_ui_server as ui
import driver_radio_native as native
import driver_radio_providers as providers
import driver_radio_admin as admin

ROOT=Path(__file__).resolve().parents[1]


class RadioFixture:
    def __init__(self,solo=True,coaching=False):self.solo=solo;self.coaching=coaching
    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory();self.practice=None
        if self.coaching:
            from test_driver_coach_service import Fixture as Practice
            self.practice=Practice(Path(self.temp.name)/'solo-pov');self.f=self.practice.radio
            self.practice.service.profile='solo-pov';self.f.service.coach=self.practice.service
            import driver_coach_history
            self.practice.service.journal=driver_coach_history.Store(self.f.access.path.parent,clock=lambda:self.practice.wall)
        else:self.f=Fixture(Path(self.temp.name)/'solo-pov')
        self.f.poll()
        self.workers=[]
        def start(body):w=Worker();self.workers.append(w);return w
        self.native=native.Supervisor(self.f.service,Path(self.temp.name)/'machine',microphone=lambda:'Synthetic microphone',
                                      worker=start,player=lambda *args:Player(),ready=lambda:True,
                                      clock=lambda:self.practice.at if self.practice else self.f.at)
        self.native.step()
        m=console.m;self.template=patch.object(m,'solo_template',return_value='pov');self.template.start();crew=m.CrewSource('');crew.inject_row(1,name='Director',director=True)
        relay=m.Relay(console._FakeSource([],[]),[53001,53002],self.temp.name,solo=self.solo)
        telemetry=m.gt7_telemetry.TelemetryStore();telemetry.engineer=SimpleNamespace(snapshot=lambda compact=False:copy.deepcopy(self.f.snap))
        self.crew=crew;self.telemetry=telemetry
        if self.practice:telemetry.engineer.detail=self.practice.detail
        self.engineer=m.race_engineer_access.Store(self.f.access.path.parent,True)
        engineer=self.engineer
        handler=m.make_handler(relay,panel_path=str(ROOT/'src/director/director-panel.html'),console_page_path=str(ROOT/'src/console/console.html'),
            console_secret=console.SECRET,crew_source=crew,engineer_access=engineer,telemetry_store=telemetry,
            console_versions_path=str(Path(self.temp.name)/'console-versions.json'),
            radio_service=self.f.service,radio_native=self.native)
        self.server=m.ThreadingHTTPServer(('127.0.0.1',0),handler);threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.token=m.console_auth.mint_token(console.SECRET,'driver')
        self.enricher=providers.Enricher(self.f.service)
        context=ui._ctx();context['version']='dev';context['driver_radio']=self.admin
        context['status']=lambda:dict(ok=True,version='dev',relay={'alive':False,'http_ok':False,'port':8088},
                                    streams=[],companion={'running':False,'url':''},apps_running={},tailscale_ip=None)
        context['update_check']=lambda force=False:dict(ok=True,current='dev',latest=None,update_available=False,note='development build')
        context['engineer_read']=lambda body:dict(ok=True,profile='solo-pov',available=True,enabled=False,revision=0,grants=[],recordings=[])

        context['profiles']=lambda:dict(ok=True,active='solo-pov',profiles=[dict(name='solo-pov',display='Solo POV demo',kind='solo',template='pov')])
        self.cc,self.cc_port=ui._serve(context)
        self.cc_url='http://127.0.0.1:'+str(self.cc_port)
        return self
    def admin(self,body=None,native=False,coach=False):
        if coach:
            if body is not None and body.get('expectedProfile')!='solo-pov':return dict(ok=False,error='Profile changed')
            try:
                value=None if body is None else {k:v for k,v in body.items() if k!='expectedProfile'}
                return dict(ok=True,profile='solo-pov',result=admin.Client('solo-pov',self.server.server_port).request(coach=True,body=value))
            except ValueError as error:return dict(ok=False,error=str(error),code=getattr(error,'code',None))
        if body is None:return dict(ok=True,profile='solo-pov',settings=self.f.access.read(),native=self.native.status(),agents=[])
        if body.get('expectedProfile')!='solo-pov':return dict(ok=False,error='Profile changed')
        try:
            value={k:v for k,v in body.items() if k!='expectedProfile'}
            result=self.native.control(value) if native else self.f.access.update(value)
            return dict(ok=True,result=result)
        except ValueError:return dict(ok=False,error='Invalid or conflicting radio settings')
    def request(self,path,method='GET',body=None,subject='driver',cc=False,headers=None):
        port=self.cc_port if cc else self.server.server_port
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        head={'Content-Type':'application/json'}
        if subject:head['Cookie']='rc_console='+console.m.console_auth.mint_token(console.SECRET,subject)
        head.update(headers or {})
        try:
            conn.request(method,path,body=None if body is None else json.dumps(body),headers=head)
            response=conn.getresponse();raw=response.read();return response.status,raw,dict(response.getheaders())
        finally:conn.close()
    def __exit__(self,*args):
        if self.practice:self.practice.service.close()
        self.enricher.close();self.native.close();self.f.service.close()
        for server in (self.cc,self.server):server.shutdown();server.server_close()
        self.template.stop();self.temp.cleanup()


def features(engineer=False,coach=False,agent=None):
    return {'engineer':{'enabled':engineer,'agent':agent},'coach':{'enabled':coach,'agent':agent}}


def switch(f,**kwargs):
    config=f.f.access.read();return f.f.access.update({'expected':config['revision'],'features':features(**kwargs)})


def t_page_launcher_and_static_component_follow_the_feature_switches():
    with RadioFixture() as f:
        status,raw,headers=f.request('/console/driver-radio')
        assert status==200 and b'DriverRadio.mount' in raw and headers['Cache-Control']=='no-store'
        assert 'HttpOnly' in headers['Set-Cookie'] and 'Path=/console' in headers['Set-Cookie']
        status,raw,_=f.request('/console/whoami');me=json.loads(raw)
        assert me['driver_radio'] is True and me['ai_engineer'] is True and me['ai_coach'] is True,me
        for path in ('/console/driver-radio','/console/driver-radio/native','/console/driver-radio/snapshot'):
            status,raw,_=f.request(path,subject='director');assert status==200,(path,status,raw)
        assert f.request('/console/race-engineer/access')[0]==403
        status,raw,_=f.request('/console/driver-radio-ui.js',subject=None)
        assert status==200 and b'class View' in raw and b'console.SECRET' not in raw
        switch(f,coach=True)  # The page hosts both features: the coach alone still opens it.
        assert f.request('/console/driver-radio')[0]==200
        me=json.loads(f.request('/console/whoami')[1])
        assert me['driver_radio'] is True and me['ai_engineer'] is False and me['ai_coach'] is True,me
        switch(f,engineer=True)  # The coach view re-checks its own switch, not the page's.
        me=json.loads(f.request('/console/whoami')[1])
        assert me['driver_radio'] is True and me['ai_engineer'] is True and me['ai_coach'] is False,me
        switch(f,coach=True)
        status,raw,_=f.request('/console/driver-radio/snapshot')
        assert status==403 and json.loads(raw)['error']['code']=='feature_disabled'
        switch(f)
        status,raw,_=f.request('/console/driver-radio')
        assert status==403 and json.loads(raw)['error']['code']=='feature_disabled'
        me=json.loads(f.request('/console/whoami')[1])
        assert me['driver_radio'] is False and me['ai_engineer'] is False and me['ai_coach'] is False,me


def t_any_signed_in_console_subject_reaches_the_engineer_without_an_assignment():
    with RadioFixture() as f:
        switch(f,engineer=True)
        status,raw,_=f.request('/console/driver-radio/snapshot',subject='never-assigned')
        assert status==200,(status,raw)
        switch(f,coach=True)
        status,raw,_=f.request('/console/driver-radio/snapshot',subject='never-assigned')
        doc=json.loads(raw)['error']
        assert status==403 and doc['code']=='feature_disabled' and doc['message']=='Enable the AI Race Engineer for this profile',(status,doc)
        switch(f,engineer=True)
        status,raw,_=f.request('/console/driver-radio/snapshot',subject=None)
        assert status==401 and json.loads(raw)['error']['code']=='unauthorized',(status,raw)


def t_producer_proxy_rejects_foreign_origin_wrong_profile_and_stale_revision():
    with RadioFixture() as f:
        status,raw,_=f.request('/api/driver-radio',cc=True);assert status==200 and json.loads(raw)['profile']=='solo-pov'
        status,raw,_=f.request('/api/driver-radio',method='POST',body={'expectedProfile':'solo-pov'},cc=True,headers={'Origin':'https://foreign.invalid'})
        assert status==403 and json.loads(raw)['ok'] is False
        status,raw,_=f.request('/api/driver-radio',method='POST',body={'expectedProfile':'other'},cc=True)
        assert status==400 and json.loads(raw)['ok'] is False
        status,raw,_=f.request('/api/driver-radio',method='POST',body={'expectedProfile':'solo-pov','expected':0,'features':features()},cc=True)
        assert status==400 and json.loads(raw)['ok'] is False
        assert f.f.access.enabled('engineer') is True
        client=admin.Client('solo-pov',f.server.server_port)
        assert client.request()['protocol']==admin.PROTOCOL
        try:admin.Client('other',f.server.server_port).request()
        except ValueError as exc:assert 'profile' in str(exc)
        else:raise AssertionError('Foreign relay accepted')


def t_producer_read_reports_an_unavailable_relay_without_a_request_error():
    with RadioFixture() as f,patch.object(f,'admin',return_value={'ok':False,'error':'Start the relay for this profile'}):
        # The handler captured the bound callback when the fixture was created.
        context=ui._ctx();context['driver_radio']=f.admin
        server,port=ui._serve(context)
        try:
            for path in ('/api/driver-radio','/api/driver-radio/coach'):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                conn.request('GET',path)
                response=conn.getresponse();result=json.loads(response.read());conn.close()
                assert response.status==200 and result=={'ok':False,'error':'Start the relay for this profile'}
        finally:server.shutdown();server.server_close()


def t_packaged_component_is_served_and_cached_before_bundle_cleanup():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);(root/'ui').mkdir()
        page=root/'ui/control-center.html';page.write_text('<html>Control Center</html>',encoding='utf8')
        script=root/'driver-radio.js';expected=(ROOT/'src/console/driver-radio.js').read_bytes();script.write_bytes(expected)
        context=ui._ctx();context['page_path']=str(page)
        server,port=ui._serve(context)
        try:
            script.unlink()
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            conn.request('GET','/driver-radio-ui.js')
            response=conn.getresponse();raw=response.read();conn.close()
            assert response.status==200 and raw==expected
            assert response.getheader('Content-Type').startswith('application/javascript')
        finally:server.shutdown();server.server_close()


def t_control_center_proxy_checks_owned_relay_and_profile_before_writes():
    from unittest.mock import patch
    import test_racecast
    m=test_racecast.m
    with RadioFixture() as f,patch.object(m,'_active_profile_name',return_value='solo-pov'), \
         patch.object(m,'_running_relay_profile',return_value='solo-pov'),patch.object(m.sv,'read_pid',return_value=12345), \
         patch.object(m.sv,'pid_is_relay',return_value=True),patch.object(m,'RELAY_PORT',f.server.server_port), \
         patch.object(m,'_runtime_base_dir',return_value=f.temp.name):
        result=m.driver_radio_data();assert result['ok'] and result['profile']=='solo-pov'
        result=m.driver_radio_data({'expectedProfile':'solo-pov','expected':0,'features':features()})
        assert not result['ok'] and result['code']=='conflict'
        with patch.object(m.sv,'pid_is_relay',return_value=False),patch.object(admin,'Client') as client:
            assert not m.driver_radio_data({'expectedProfile':'solo-pov','expected':1,'features':features()})['ok']
            client.assert_not_called()
        with patch.object(admin,'Client') as client:
            assert not m.driver_radio_data({'expectedProfile':'other','expected':1,'features':features()})['ok']
            client.assert_not_called()
        assert f.f.access.enabled('engineer') is True


def _coach_js(body):
    """Run CoachView (extracted from the browser file) under node with a minimal fake DOM."""
    import shutil,subprocess
    node=shutil.which('node')
    if not node:
        print('  (node not installed, JS check skipped)');return None
    src=(ROOT/'src/console/driver-radio.js').read_text(encoding='utf-8')
    helpers=src[src.index('function el('):src.index('const COMPOUNDS')]
    klass=src[src.index('class CoachView'):src.index('window.DriverRadio')]
    harness=r"""
const els={};
const mk=()=>({textContent:'',value:'',disabled:false,hidden:false,checked:false,open:false,dataset:{},children:[],
  append(...c){this.children.push(...c);},replaceChildren(...c){this.children=c;},querySelectorAll(){return [];},
  get options(){return this.children;}});
const host={querySelector:sel=>els[sel]||(els[sel]=mk()),replaceChildren(){}};
class Option{constructor(label,value){this.label=label;this.value=value;}}
const document={createElement:()=>mk()};
"""
    out=subprocess.run([node,'-'],input=harness+helpers+klass+body,capture_output=True,text=True,
                       encoding='utf-8',errors='replace',check=True,timeout=30)
    return out.stdout


def t_coach_buttons_survive_a_transient_failure():
    out=_coach_js(r"""
const view=Object.create(CoachView.prototype);
Object.assign(view,{host,admin:true,compact:false,profile:()=>null,sending:false,stopping:false,dirty:false,controllers:new Set(),
  actionError:'',generation:0,editVersion:0});
view.state={version:1,generation:'g',profile:'p',viewer:'v',revision:3,epoch:1,allow_ai:false,active:true,pending:false,phase:'ready',
  calibration:{phase:'ready',mode:'practice',objective:'balanced'},focus:{focus:'braking',label:'Brake point',action:'Brake later',progress:{text:'1 of 3'}}};
const ids=['start','stop','goal','set-skill','use-reference','skip','keep','show-evidence','mode','objective','ai','skill','reference-choice'];
const snap=()=>ids.map(id=>id+':'+view.q(id).disabled).join(',');
view.render();const before=snap();
view.fail(Object.assign(new Error(''),{status:503,code:'coaching_unavailable'}));
view.render();const after=snap();
console.log(JSON.stringify({before,after,error:view.q('error').textContent,phase:view.state&&view.state.phase,active:view.state&&view.state.active}));
view.fail(Object.assign(new Error('Access changed.'),{status:403,code:'forbidden'}));
const cleared403=view.state===null;
view.state={version:1,generation:'g',profile:'p',viewer:'v',revision:3,active:true,pending:false,phase:'ready'};
view.fail(Object.assign(new Error('Signed out.'),{status:401}));
console.log(JSON.stringify({cleared:cleared403,cleared401:view.state===null}));
""")
    if out is None:return
    first,second=[json.loads(line) for line in out.splitlines()]
    assert first['before']==first['after'],"a transient failure keeps the last controls"
    assert first['error']=='Coaching is unavailable.',first['error']
    assert first['phase']=='ready' and first['active'] is True
    assert second['cleared'] is True
    assert second['cleared401'] is True,'a 401 without a code still clears the view'


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

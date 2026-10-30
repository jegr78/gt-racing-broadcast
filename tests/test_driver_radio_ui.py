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


def t_messages_and_native_serve_either_feature_and_only_enabled_sources():
    import driver_radio_access as access
    def lane(f):
        status,raw,_=f.request('/console/driver-radio/messages');doc=json.loads(raw)
        return status,doc.get('messages',doc.get('error'))
    def hint(messages,feature):
        # A switched-off feature's refusal passes the lane (the driver view shows it in the
        # Voice row); it carries only the fixed hint text, no engineer or coach data.
        rows=[m for m in messages if m['source']==feature]
        assert [m['kind'] for m in rows]==['refusal'] and rows[0]['text']==access.DISABLED[feature],rows
        assert all(not value for value in rows[0]['evidence'].values()),rows[0]
    owner={'profile':'solo-pov','subject':'local:producer'}
    with RadioFixture() as f:
        switch(f,coach=True);f.f.poll()
        assert f.f.service.publish_coach('Brake later into turn one.',{},owner,'training',authorize=lambda:True,ready=lambda:True)
        f.f.service.refusal('engineer',f.f.service.refresh()['binding'])
        status,messages=lane(f)
        assert status==200 and [m['source'] for m in messages]==['coach','engineer'],(status,messages)
        hint(messages,'engineer')
        status,raw,_=f.request('/console/driver-radio/native');assert status==200 and json.loads(raw)['enabled'] in (True,False),(status,raw)
        for path in ('/console/driver-radio/snapshot','/console/driver-radio/setup'):
            status,raw,_=f.request(path);assert status==403 and json.loads(raw)['error']['code']=='feature_disabled',(path,status)
    with RadioFixture() as f:
        switch(f,engineer=True);f.f.poll()
        f.f.request('fuel');f.f.service.refusal('coach',f.f.service.refresh()['binding'])
        status,messages=lane(f)
        assert status==200 and {m['source'] for m in messages}=={'engineer','coach'},(status,messages)
        assert any(m['source']=='engineer' and m['kind']!='refusal' for m in messages),messages
        hint(messages,'coach')
        # The engineer's own answers stay in the relay once it is switched off.
        switch(f,coach=True);f.f.poll()
        status,messages=lane(f)
        assert status==200 and not [m for m in messages if m['source']=='engineer' and m['kind']!='refusal'],(status,messages)
        switch(f);f.f.poll()
        for path in ('/console/driver-radio/messages','/console/driver-radio/native'):
            status,raw,_=f.request(path);doc=json.loads(raw)['error']
            assert status==403 and doc['code']=='feature_disabled' and 'AI Race Engineer' in doc['message'] and 'AI Coach' in doc['message'],(path,status,doc)


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
    helpers=src[src.index('function el('):src.index('class View')]   # every top-level helper
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


def t_coach_badge_says_unavailable_while_a_load_error_shows():
    out=_coach_js(r"""
const view=Object.create(CoachView.prototype);
Object.assign(view,{host,admin:false,compact:false,profile:()=>null,sending:false,stopping:false,dirty:false,controllers:new Set(),
  actionError:'',generation:0,editVersion:0,off:false,state:null});
const phase=()=>view.q('phase').textContent;
view.render();const connecting=phase();
// A failed first load: the error line and the badge agree, without waiting for another render.
view.fail(Object.assign(new Error('Live coaching is unavailable for this source'),{status:503,code:'source_unavailable'}));
const failed=[phase(),view.q('error').textContent];
// A failed poll after a good state keeps the controls but no longer claims the old phase.
view.state={version:1,generation:'g',profile:'p',viewer:'v',revision:3,active:true,pending:false,phase:'ready'};view.loadError=false;
view.render();const ready=phase();
view.fail(Object.assign(new Error(''),{status:503}));const transient=phase();
// A rejected action is not a load failure: the badge keeps the current phase.
view.loadError=false;view.render();
view.fail(Object.assign(new Error('changed'),{status:409,code:'conflict'}),true);const action=phase();
console.log(JSON.stringify({connecting,failed,ready,transient,action}));
""")
    if out is None:return
    got=json.loads(out)
    assert got['connecting']=='Connecting',got
    assert got['failed']==['Unavailable','Live coaching is unavailable for this source'],got
    assert got['ready']=='Ready' and got['transient']=='Unavailable' and got['action']=='Ready',got


# The shared component under node with a minimal fake DOM: every element that the
# component's HTML names by id or class is registered, unknown ids are created on demand,
# and fetch answers from a per-scenario route table. Skipped where node is not installed.
_FAKE_DOM=r"""
const registry={},classes={},written=[];
class El{
 constructor(tag){this.tagName=String(tag).toUpperCase();this.children=[];this._text='';this.hidden=false;this.disabled=false;this.checked=false;this.value='';this.dataset={};this.options=[];this.className='';
  this.classList={toggle:(n,on)=>{this['_cls_'+n]=on;}};}
 set textContent(v){this._text=String(v);this.children=[];}
 get textContent(){return [this._text,...this.children.map(c=>c.textContent)].filter(Boolean).join(' ');}
 set innerHTML(html){this.children=[];register(html);}
 insertAdjacentHTML(_where,html){register(html);}
 append(...nodes){for(const n of nodes){if(this.tagName==='SELECT')this.options.push(n);this.children.push(n);}}
 prepend(...nodes){this.children.unshift(...nodes);}
 replaceChildren(...nodes){this.children=[];this.options=[];this.append(...nodes);}
 querySelector(sel){return sel[0]==='#'?byId(sel.slice(1)):sel[0]==='.'?(classes[sel.slice(1)]||new El('div')):new El('div');}
 querySelectorAll(){return [];}
 getElementById(id){return byId(id);}
 attachShadow(){return this.shadowRoot=new El('root');}
 scrollIntoView(){}
}
function byId(id){return registry[id]||(registry[id]=new El('div'));}
function register(html){
 written.push(html);
 for(const match of html.matchAll(/<(\w+)([^>]*)>/g)){
  const attrs=match[2],id=/\bid="([^"]+)"/.exec(attrs),cls=/\bclass="([^"]+)"/.exec(attrs);
  if(!id&&!cls)continue;const node=new El(match[1]);node.hidden=/\shidden(\s|$)/.test(attrs+' ');
  const text=/^([^<]*)</.exec(html.slice(match.index+match[0].length));if(text)node._text=text[1];
  if(id)registry[id[1]]=node;if(cls&&!classes[cls[1]])classes[cls[1]]=node;
 }
}
class Option extends El{constructor(text,value){super('option');this.textContent=text;this.value=value;}}
const document={createElement:tag=>new El(tag)};
const window={addEventListener(){},removeEventListener(){}};
const routes={},posts=[];
async function fetch(url,options={}){
 if(options.method==='POST')posts.push({url,body:JSON.parse(options.body)});
 const [status,data]=routes[url]||[404,{error:{message:'missing '+url}}];
 return {ok:status<300,status,json:async()=>JSON.parse(JSON.stringify(data))};
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,30));
"""


def _run_component(scenario):
    import shutil,subprocess
    node=shutil.which('node')
    if not node:
        print('  (node not installed, JS check skipped)');return None
    script=(ROOT/'src/console/driver-radio.js').read_text(encoding='utf8')
    program=_FAKE_DOM+script+'\n(async()=>{'+scenario+'\n})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1);});'
    out=subprocess.run([node,'-'],input=program,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    assert out.returncode==0,out.stderr
    return json.loads(out.stdout)


_BINDING={'generation':'g','session_id':'s','context_revision':1}
_COACH_STATE={'version':1,'revision':1,'profile':'solo-pov','viewer':'console:driver','generation':'c1','phase':'inactive','active':False}


def _driver_routes(messages=(),engineer=True,coach=True):
    def off(name):return [403,{'error':{'code':'feature_disabled','message':'Enable the '+name+' for this profile'}}]
    radio={'/console/driver-radio/snapshot':[200,{'epoch':1,'binding':_BINDING,'live':True,'assessment':{}}],
           '/console/driver-radio/messages':[200,{'epoch':1,'binding':_BINDING,'messages':list(messages)}],
           '/console/driver-radio/native':[200,{'binding':_BINDING,'revision':0,'phase':'idle','enabled':True,'paused':False,'muted':False,'volume':1,'input_language':'en'}],
           '/console/driver-radio/setup':[200,{'binding':_BINDING,'revision':0,'setup':{}}]}
    # The relay serves messages (enabled sources and refusal hints) and native voice for
    # either feature.
    shared=('/console/driver-radio/messages','/console/driver-radio/native')
    keep={name for name,on in (('engineer',engineer),('coach',coach)) if on}
    radio['/console/driver-radio/messages'][1]['messages']=[m for m in messages if m['source'] in keep or m['kind']=='refusal']
    if not engineer:radio={path:(doc if coach and path in shared else off('AI Race Engineer')) for path,doc in radio.items()}
    radio['/console/driver-radio/coach']=[200,_COACH_STATE] if coach else off('AI Coach')
    radio['/console/whoami']=[200,{'subject':'driver','ai_engineer':engineer,'ai_coach':coach}]
    return json.dumps(radio)


def t_admin_form_saves_two_features():
    settings={'revision':3,'features':features(engineer=True),'voice':{'enabled':True,'microphone':None,'input_language':'en'},
              'native':{'wake':'hey_jeff','stt':'vosk'},'providers':{'speech':False}}
    data={'ok':True,'profile':'solo-pov','settings':settings,'native':{'phase':'idle','phrases':['hey_jeff']},
          'agents':[{'id':'agent-a','name':'Agent A'},{'id':'agent-b','name':'Agent B'}]}
    out=_run_component("""
     Object.assign(routes,{'/api/driver-radio':[200,%s],'/api/driver-radio/coach':[403,{ok:false,error:'Enable the AI Coach for this profile',code:'feature_disabled'}]});
     const view=window.DriverRadio.mountAdmin(new El('div'));await tick();
     // The first profile read restarts coaching once; poll it again now instead of in 3 s.
     clearTimeout(view.coach.timer);await view.coach.poll();
     byId('ai-engineer-enabled').checked=true;byId('ai-engineer-agent').value='agent-a';
     byId('ai-coach-enabled').checked=true;byId('ai-coach-agent').value='agent-b';
     byId('admin-form').onsubmit({preventDefault(){}});await tick();
     const form=written.find(html=>html.includes('id="admin-form"'));
     const result={posts,form,coachOff:byId('coach-off').hidden};view.destroy();console.log(JSON.stringify(result));
    """%json.dumps(data))
    if out is None:return
    body=out['posts'][0]['body']
    assert body['features']=={'engineer':{'enabled':True,'agent':'agent-a'},'coach':{'enabled':True,'agent':'agent-b'}},body
    assert 'drivers' not in json.dumps(body) and body['expected']==3 and body['expectedProfile']=='solo-pov',body
    cards=[out['form'].find('<h3>'+title+'</h3>') for title in ('AI Race Engineer','AI Coach','Voice')]
    assert -1 not in cards and cards==sorted(cards),cards
    assert out['coachOff'] is False,'A disabled AI Coach must show its Off line in the Control Center'


def t_sections_split_messages_by_source():
    messages=[dict(id=1,source='engineer',kind='requested',state='done',text='Fuel for 12 laps.'),
              dict(id=2,source='coach',kind='coach',state='done',text='Brake two metres later.'),
              dict(id=3,source='coach',kind='refusal',state='text_only',text='Enable the AI Coach for this profile'),
              dict(id=4,source='engineer',kind='refusal',state='text_only',text='Enable the AI Race Engineer for this profile',epoch=1)]
    out=_run_component("""
     Object.assign(routes,%s);
     const view=window.DriverRadio.mount(new El('div'));await tick();await tick();
     const result={engineer:byId('history').textContent,coach:byId('coach-messages').textContent,latest:byId('latest').textContent,
       hint:byId('voice-hint').textContent};
     view.destroy();console.log(JSON.stringify(result));
    """%_driver_routes(messages))
    if out is None:return
    assert 'Fuel for 12 laps.' in out['engineer'] and 'Fuel for 12 laps.' not in out['coach'],out
    assert 'Brake two metres later.' in out['coach'] and 'Brake two metres later.' not in out['engineer'],out
    # Refusal hints belong to the shared Voice row, not to either feature's section.
    for section in ('engineer','coach'):
        assert 'Feature off' not in out[section] and 'Enable the' not in out[section],(section,out)
    assert out['latest']=='Fuel for 12 laps.',out
    assert out['hint']=='Feature off Enable the AI Race Engineer for this profile',out


def t_a_disabled_feature_turns_off_only_its_own_section():
    probe="""
     const view=window.DriverRadio.mount(new El('div'));await tick();await tick();
     const result={content:byId('content').hidden,engineerOff:byId('engineer-off').hidden,engineerBody:byId('engineer-body').hidden,
       coachOff:byId('coach-off').hidden,coachBody:byId('coach-body').hidden,coachState:!!view.coach.state,
       problem:byId('problem').textContent,engineerText:byId('engineer-off').textContent,coachText:byId('coach-off').textContent};
     view.destroy();console.log(JSON.stringify(result));"""
    out=_run_component('Object.assign(routes,%s);'%_driver_routes(engineer=False)+probe)
    if out is None:return
    assert out['content'] is False and out['engineerOff'] is False and out['engineerBody'] is True,out
    assert out['coachOff'] is True and out['coachBody'] is False and out['coachState'] is True,out
    assert out['engineerText']=='Off · enable it in the Control Center' and out['problem']=='',out
    out=_run_component('Object.assign(routes,%s);'%_driver_routes(coach=False)+probe)
    assert out['engineerOff'] is True and out['engineerBody'] is False,out
    assert out['coachOff'] is False and out['coachBody'] is True and out['coachState'] is False,out
    assert out['coachText']=='Off · enable it in the Control Center',out


def t_coach_only_view_keeps_the_voice_controls_and_coach_messages():
    messages=[dict(id=1,source='coach',kind='coach',state='done',text='Brake two metres later.')]
    out=_run_component("""
     Object.assign(routes,%s);
     const view=window.DriverRadio.mount(new El('div'));await tick();await tick();
     const result={voice:byId('voice-section').hidden,pause:byId('pause').disabled,phase:byId('phase').textContent,
       coach:byId('coach-messages').textContent,engineerOff:byId('engineer-off').hidden,content:byId('content').hidden};
     view.destroy();console.log(JSON.stringify(result));
    """%_driver_routes(messages,engineer=False))
    if out is None:return
    assert out['content'] is False and out['engineerOff'] is False,out
    assert out['voice'] is False and out['pause'] is False and out['phase'].startswith('Native voice'),out
    assert 'Brake two metres later.' in out['coach'],out


def t_a_refusal_shows_in_the_voice_row_where_the_driver_looks():
    # Coach alone: asking the switched-off AI Race Engineer by voice gets its hint as text in
    # the Voice row (its own section only says Off), also when speech is muted or not live.
    messages=[dict(id=1,source='coach',kind='coach',state='done',text='Brake two metres later.'),
              dict(id=2,source='engineer',kind='refusal',state='text_only',text='Enable the AI Race Engineer for this profile',epoch=1)]
    probe="""
     Object.assign(routes,%s);
     const view=window.DriverRadio.mount(new El('div'));await tick();await tick();
     const result={hint:byId('voice-hint').textContent,hidden:byId('voice-hint').hidden,voice:byId('voice-section').hidden,
       coach:byId('coach-messages').textContent,engineerOff:byId('engineer-off').hidden};
     view.destroy();console.log(JSON.stringify(result));"""
    out=_run_component(probe%_driver_routes(messages,engineer=False))
    if out is None:return
    assert out['voice'] is False and out['hidden'] is False and out['engineerOff'] is False,out
    assert out['hint']=='Feature off Enable the AI Race Engineer for this profile',out
    assert 'Enable the' not in out['coach'],out
    # A later message replaces the hint: it answers that one request, not every poll.
    later=messages+[dict(id=3,source='coach',kind='coach',state='done',text='Lift earlier into turn four.')]
    out=_run_component(probe%_driver_routes(later,engineer=False))
    assert out['hidden'] is True and out['hint']=='',out


def t_a_native_refusal_with_both_features_off_hides_the_voice_row():
    out=_run_component("""
     Object.assign(routes,%s);
     const view=window.DriverRadio.mount(new El('div'));await tick();await tick();
     const before=byId('voice-section').hidden;
     routes['/console/driver-radio/native']=[403,{error:{code:'feature_disabled',message:'Enable the AI Race Engineer or the AI Coach for this profile'}}];
     clearTimeout(view.timer);await view.send('/native',{binding:view.native.binding,expected:0,muted:true});
     const result={before,voice:byId('voice-section').hidden,native:view.native,pause:byId('pause').disabled,engineerOff:byId('engineer-off').hidden};
     view.destroy();console.log(JSON.stringify(result));
    """%_driver_routes(engineer=False))
    if out is None:return
    assert out['before'] is False,out
    assert out['voice'] is True and out['native'] is None and out['pause'] is True and out['engineerOff'] is False,out


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

"""Synthetic producer UI and real signed Console on private ephemeral ports."""
import threading
import json
import urllib.request
from pathlib import Path

from ai_ui_fixture import Fixture as Base
import test_console_gate as gate
import race_engineer_access


class Fixture(Base):
    def __init__(self,root):
        super().__init__(root)
        self.crew=gate.m.CrewSource('')
        self.crew.inject_row(1,name='Engineer',race_engineer=True)
        self.crew.inject_row(2,name='Producer',producer=True,commentator=True)
        self.writes=[]
        self.console_server=None

    def crew_rpc(self,path,payload=None):
        req=urllib.request.Request('http://127.0.0.1:'+str(self.console_server.server_port)+path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)

    def access(self):
        return race_engineer_access.Store(self.root/self.profile,available=True)

    def engineer(self,write,payload):
        try:
            if payload.get('profile')!=self.profile:
                raise race_engineer_access.AccessError('profile_changed','Active profile changed')
            store=self.access()
            doc=store.update(**{k:payload[k] for k in ('enabled','grant','revoke','label','expected') if k in payload}) if write else store.read()
            return dict(ok=True,profile=self.profile,available=True,enabled=doc['enabled'],revision=doc['revision'],analysis_available=False,
                grants=[dict(id=k,label=v['label'],context_shared=True) for k,v in doc['grants'].items()],recordings=store.recordings())
        except race_engineer_access.AccessError as exc:return dict(ok=False,error=dict(code=exc.code,message=str(exc)))

    def context(self):
        ctx=super().context()
        ctx['profiles']=lambda:dict(ok=True,active=self.profile,profiles=[dict(name=n,display='Engineer practice' if n=='profile' else 'Other practice',sheet_set=False,kind='solo',template='pov') for n in ('profile','other')])
        original_recordings=ctx['telemetry_recordings']
        def recordings():
            data=original_recordings()
            for row in data['recordings']:row.update(duration_s=201,size=Path(self.source.path).stat().st_size)
            return data
        ctx['telemetry_recordings']=recordings
        ctx['engineer_read']=lambda data:self.engineer(False,data)
        ctx['engineer_write']=lambda data:self.engineer(True,data)
        def crew_read():
            if self.console_server is not None:
                data=self.crew_rpc('/crew/data')
                return dict(ok=True,entries=[dict(row=i,**row) for i,row in enumerate(data['rows'],1)])
            return dict(ok=True,entries=[dict(row=i,name=n,director=d,producer=p,commentator=c,race_control=rc,discord=x,race_engineer=re)
                for i,(n,d,p,c,rc,x,re) in enumerate(self.crew.get_members(),1)])
        def crew_write(row,name,director,producer,commentator=False,race_control=False,discord='',race_engineer=None):
            if self.console_server is not None:
                return self.crew_rpc('/crew/set',dict(row=row,name=name,director=director,producer=producer,
                    commentator=commentator,race_control=race_control,discord=discord,race_engineer=race_engineer))
            self.writes.append(dict(row=row,name=name,race_engineer=race_engineer,discord=discord))
            self.crew.inject_row(row,name,director,producer,commentator,race_control,discord,race_engineer)
            return dict(ok=True)
        ctx.update(crew_read=crew_read,crew_write=crew_write)
        return ctx

    def console(self, with_live=False, solo=False):
        m=gate.m
        relay=m.Relay(gate._FakeSource([],[]),[53001,53002],str(self.root),solo=solo)
        self.relay = relay
        control=m.SetupControl('https://example.test',None,crew_source=self.crew)
        def push(payload,label):self.writes.append(payload);return True,None
        control._sync_push=push
        telemetry = None
        if with_live:
            import race_engineer_live as live
            import race_engineer_api as api
            from test_race_engineer_live import FakeTracks, FakeCars, feed, write_circle_recording
            path = write_circle_recording(str(self.root/self.profile/'telemetry-recordings'))
            self.live_clock = 10.0
            self.live = live.Store(lambda: self.access().read()['enabled'], tracks=FakeTracks(), cars=FakeCars(),
                runtime_base=str(self.root), queue_max=4096, clock=lambda: self.live_clock,
                context_source=lambda: gate.m.gt7_context.Store.prepared(str(self.root/self.profile/'telemetry-recordings')).read())
            self.live.api = api.Service(self.live)
            self.live_path = path
            self.feed_live = lambda: (self.live.refresh_enabled(), feed(self.live, self.live_path))
            telemetry = gate.m.gt7_telemetry.TelemetryStore(engineer=self.live)
        self.telemetry = telemetry
        self.console_access = self.access()
        handler=m.make_handler(relay, panel_path=str(Path(gate.ROOT)/"src/director/director-panel.html"),
            hud_path=str(Path(gate.ROOT)/"src/obs/hud.html"),
            hud_source=m.HudSource("", "", str(self.root/"hud-cache.json")), console_secret=gate.SECRET,crew_source=self.crew,setup_ctl=control,
            chat_store=m.ChatStore(str(self.root/'chat.json')),
            console_page_path=str(gate.ROOT+'/src/console/console.html'),
            telemetry_store=telemetry, engineer_access=self.console_access,engineer_page_path=str(Path(gate.ROOT)/'src/console/race-engineer.html'))
        server=m.ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.console_server=server
        threading.Thread(target=server.serve_forever,daemon=True).start()
        return server

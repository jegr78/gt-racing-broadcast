#!/usr/bin/env python3
"""Shared radio state, priority, bounded work and cancellation acknowledgement."""
import copy
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_access as access
import driver_radio_assessment as assessment
import driver_radio_service as radio
from test_driver_radio_assessment import snapshot


class Fixture:
    def __init__(self,d, snap=None):
        self.at=100.0
        self.snap=snap or snapshot((90,90,90,90,90,90))
        self.access=access.Store(d,True)
        self.access.update({'expected':0,'enabled':True,'drivers':['driver'],
                            'voice':{'enabled':True,'microphone':None,'input_language':'en'}})
        self.session=access.Session()
        self.service=radio.Service(self.access,self.session,lambda:copy.deepcopy(self.snap),clock=lambda:self.at)
    def poll(self):return self.service.refresh(force=True)
    def request(self,preset='fuel'):
        return self.service.request(preset,assessment.binding(self.snap))


def error(fn,code):
    try:fn()
    except access.RadioError as exc:assert exc.code==code,(exc.code,code)
    else:raise AssertionError('Expected '+code)


def t_shared_requests_dedupe_and_one_playback_owner():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll()
        first=f.request();second=f.request()
        assert first['id']==second['id']
        assert len(f.service.history()['messages'])==1
        tickets=[]
        workers=[threading.Thread(target=lambda:tickets.append(f.service.take_playback())) for _ in range(4)]
        for w in workers:w.start()
        for w in workers:w.join()
        owned=[t for t in tickets if t is not None]
        assert len(owned)==1
        ticket=owned[0]
        assert not ticket.cancelled.is_set() and ticket.message['id']==first['id']
        assert not f.service.finish_playback('forged')
        assert f.service.take_playback() is None
        assert f.service.finish_playback(ticket.id)
        assert f.service.take_playback() is None, 'message must not replay after completion'


def t_fuel_crossings_refuel_hysteresis_and_urgent_preemption():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();routine=f.request('pace')
        ticket=f.service.take_playback();assert ticket.message['id']==routine['id']
        f.snap['instruments']['fuel_l']=6;f.snap['revision']+=1;f.poll()
        messages=f.service.history()['messages']
        assert messages[-1]['severity']=='warning'
        for _ in range(10):f.poll()
        assert len(f.service.history()['messages'])==2
        f.snap['instruments']['fuel_l']=3;f.snap['revision']+=1;f.poll()
        assert ticket.cancelled.is_set(), 'urgent fuel must cancel the active routine'
        assert f.service.take_playback() is None, 'old owner has not acknowledged stop'
        f.service.finish_playback(ticket.id)
        urgent=f.service.take_playback()
        assert urgent.message['severity']=='urgent'
        f.service.finish_playback(urgent.id)
        f.snap['instruments']['fuel_l']=6.1;f.snap['revision']+=1;f.poll()
        f.snap['instruments']['fuel_l']=6;f.snap['revision']+=1;f.poll()
        assert f.service.history()['messages'][-1]['severity']=='warning', 'refill must rearm'
        before=len(f.service.history()['messages'])
        f.snap['instruments']['fuel_l']=6.01;f.snap['revision']+=1;f.poll()
        f.snap['instruments']['fuel_l']=6;f.snap['revision']+=1;f.poll()
        assert len(f.service.history()['messages'])==before, 'small threshold noise must not rearm'


def t_context_pause_revocation_and_late_attachment():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();message=f.request();ticket=f.service.take_playback()
        f.snap['receipt']['state']='paused';f.poll()
        assert ticket.cancelled.is_set() and f.service.take_playback() is None
        assert not f.service.attach(message['id'],message['epoch'],{'audio':'old'})
        f.service.finish_playback(ticket.id)
        f.snap['receipt']['state']='live';f.poll()
        f.at+=3;message=f.request()
        assert f.service.attach(message['id'],message['epoch'],{'explanation':'Current explanation'})
        f.snap['context_revision']+=1;f.poll()
        assert not f.service.history()['messages']
        assert not f.service.attach(message['id'],message['epoch'],{'explanation':'Obsolete'})
        error(lambda:f.service.request('fuel',{'generation':'old'}),'conflict')
        f.access.update({'expected':1,'drivers':[]});f.poll()
        assert not f.service.history()['messages']
        assert f.service.take_playback() is None


def t_history_queue_expiry_and_pace_cooldown_are_bounded():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll()
        for n in range(40):
            f.at+=3
            f.request(assessment.PRESETS[n%4])
        history=f.service.history()
        assert len(history['messages'])<=radio.HISTORY_MAX and f.service.pending_count()<=radio.QUEUE_MAX
        f.at+=31;f.poll()
        assert f.service.take_playback() is None
        f.snap=snapshot();f.snap['revision']+=1;f.poll()
        pace=[m for m in f.service.history()['messages'] if m['kind']=='pace']
        assert len(pace)==1
        f.snap['revision']+=1;f.poll()
        assert len([m for m in f.service.history()['messages'] if m['kind']=='pace'])==1
        f.at+=61;f.snap['laps'].append(dict(f.snap['laps'][-1],id='L7',lap=7));f.snap['revision']+=1;f.poll()
        assert len([m for m in f.service.history()['messages'] if m['kind']=='pace'])==2


def t_invalid_request_limits_and_shutdown():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll()
        error(lambda:f.request('arbitrary prompt'),'invalid_body')
        f.access.update({'expected':1,'limits':{'fuel_warning_laps':6,'fuel_urgent_laps':2}})
        assert f.poll()['assessment']['fuel']['severity']=='warning'
        message=f.request('tyres');ticket=f.service.take_playback()
        assert message and ticket
        f.service.close()
        assert ticket.cancelled.is_set() and f.service.take_playback() is None
        assert not f.service.attach(message['id'],message['epoch'],{'explanation':'late'})


def t_queued_request_rechecks_its_originating_authorization():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();allowed={'first':True,'second':True}
        message=f.service.request('fuel',assessment.binding(f.snap),requester='first',authorization=lambda:allowed['first'])
        same=f.service.request('fuel',assessment.binding(f.snap),requester='second',authorization=lambda:allowed['second'])
        assert same['id']==message['id']
        ticket=f.service.take_playback()
        allowed['first']=False;f.poll()
        assert not ticket.cancelled.is_set(), 'another current requester still authorizes the shared answer'
        allowed['second']=False;f.poll()
        assert ticket.cancelled.is_set() and f.service.take_playback() is None
        assert not f.service.attach(message['id'],message['epoch'],{'explanation':'revoked answer'})
        assert f.service.finish_playback(ticket.id)


def t_evicted_active_message_stays_cancelled_until_owner_acknowledges():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll();first=f.request();ticket=f.service.take_playback()
        assert ticket.message['id']==first['id']
        for n in range(40):
            f.at+=.55
            f.request(assessment.PRESETS[(n+1)%4])
        assert first['id'] not in {m['id'] for m in f.service.history()['messages']}
        assert ticket.cancelled.is_set(), 'eviction must invalidate an active output'
        assert f.service.take_playback() is None
        assert f.service.finish_playback(ticket.id)


def t_fuel_recovery_cancels_old_warning_before_take_and_during_output():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.snap['instruments']['fuel_l']=3;f.poll()
        old=f.service.history()['messages'][-1]
        f.snap['instruments']['fuel_l']=15;f.snap['revision']+=1;f.poll()
        assert f.service.take_playback() is None, 'resolved risk must not play the old warning'
        assert not f.service.attach(old['id'],old['epoch'],{'explanation':'obsolete fuel'})
        f.snap['instruments']['fuel_l']=3;f.snap['revision']+=1;f.poll()
        ticket=f.service.take_playback();assert ticket.message['severity']=='urgent'
        f.snap['instruments']['fuel_l']=15;f.snap['revision']+=1;f.poll()
        assert ticket.cancelled.is_set() and f.service.take_playback() is None
        assert not f.service.attach(ticket.message['id'],ticket.message['epoch'],{'audio':b'old'})
        assert f.service.finish_playback(ticket.id)
        assert f.service.take_playback() is None


def t_gradual_refuel_rearms_below_the_separate_range_reset_threshold():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.snap['instruments']['fuel_l']=3;f.poll()
        ticket=f.service.take_playback();f.service.finish_playback(ticket.id)
        for fuel in (3.5,4,4.5,5,5.5,4,3):
            f.snap['instruments']['fuel_l']=fuel;f.snap['revision']+=1;f.poll()
        alerts=[m for m in f.service.history()['messages'] if m['kind']=='fuel']
        assert len([m for m in alerts if m['severity']=='urgent'])==2, alerts
        assert alerts[-1]['severity']=='urgent' and alerts[-1]['state']=='queued'


def t_native_playback_requires_explicit_profile_voice_enablement():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d)
        f.access.update({'expected':1,'voice':{'enabled':False,'microphone':None,'input_language':'en'}})
        f.poll();message=f.request()
        assert message['text'] and f.service.take_playback() is None


def t_readers_share_snapshot_work_but_attachments_recheck_current_context():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);calls=[]
        def source():calls.append(1);return copy.deepcopy(f.snap)
        f.service.source=source;f.poll()
        for _ in range(20):f.service.history()
        assert len(calls)==1, 'reader count must not multiply snapshot preparation'
        message=f.request()
        f.snap['context_revision']+=1
        assert not f.service.attach(message['id'],message['epoch'],{'explanation':'old context'})
        assert len(calls)>=3, 'attachment publication must bypass a cached context'


def t_signed_http_clients_share_messages_and_revalidate_access():
    import concurrent.futures
    import http.client
    import json
    from types import SimpleNamespace
    import test_console_gate as fixture
    m=fixture.m
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);f.poll()
        engine=SimpleNamespace(snapshot=lambda compact=False:copy.deepcopy(f.snap))
        versions=str(Path(d)/'console-versions.json')
        handler=m.make_handler(m.Relay(fixture._FakeSource([],[]),[53001,53002],d),
            console_secret=fixture.SECRET, console_versions_path=versions, engineer_access=m.race_engineer_access.Store(d,True),
            telemetry_store=SimpleNamespace(engineer=engine),radio_service=f.service)
        server=m.ThreadingHTTPServer(('127.0.0.1',0),handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        def request(path='snapshot',method='GET',body=None,subject='driver',headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            hdr={'Content-Type':'application/json'}
            if subject:hdr['Cookie']='rc_console='+m.console_auth.mint_token(fixture.SECRET,subject)
            hdr.update(headers or {})
            try:
                conn.request(method,'/console/driver-radio/'+path,
                             body=json.dumps(body) if body is not None else None,headers=hdr)
                response=conn.getresponse()
                return response.status,json.loads(response.read()),response.getheader('Cache-Control')
            finally:conn.close()
        try:
            status,state,cache=request()
            assert status==200 and state['assessment']['fuel']['range_laps']==5 and cache=='no-store'
            body={'preset':'fuel','binding':state['binding']}
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                replies=list(pool.map(lambda _:request('request','POST',body),range(4)))
            assert all(status==200 for status,_,_ in replies),replies
            assert len({doc['message']['id'] for _,doc,_ in replies})==1
            status,history,_=request('messages')
            assert status==200 and len(history['messages'])==1
            assert request('messages?since='+str(history['revision']))[1]['messages']==[]
            for path,method,payload,headers,code,want in (
                ('request','POST',{'preset':'fuel','binding':{}},{},409,'conflict'),
                ('request','POST',{'preset':'prompt','binding':state['binding']},{},400,'invalid_body'),
                ('request','POST',{'preset':'fuel','binding':state['binding'],'command':'obs'},{},400,'invalid_body'),
                ('request','POST',body,{'Origin':'https://foreign.example.test'},403,'foreign_origin'),
                ('messages?since=bad','GET',None,{},400,'invalid_query')):
                status,doc,cache=request(path,method,payload,headers=headers)
                assert status==code and doc['error']['code']==want and cache=='no-store',(status,doc)
            assert request(subject=None)[0]==401
            assert request(subject='director')[1]['error']['code']=='forbidden'
            playing=f.service.take_playback()
            m.console_admin.bump_version(versions,'driver')
            f.service.refresh(force=True)
            assert playing.cancelled.is_set(), 'queued HTTP request must observe token revocation'
            assert request('messages')[0]==401
            f.service.finish_playback(playing.id)
            m.console_admin.write_versions(versions,{})  # Restore fixture identity for the independent assignment check.
            f.access.update({'expected':1,'drivers':[]})
            status,doc,_=request('messages')
            assert status==403 and doc['error']['code']=='forbidden'
            assert f.service.history()['messages']==[]
        finally:server.shutdown();server.server_close();f.service.close()


def t_worker_runs_without_clients_and_close_stops_it():
    with tempfile.TemporaryDirectory() as d:
        f=Fixture(d);reached=threading.Event()
        def source():reached.set();return copy.deepcopy(f.snap)
        f.service.source=source
        f.service.start()
        assert reached.wait(3)
        f.service.close()
        assert f.service._thread is not None and not f.service._thread.is_alive()
        assert f.service.take_playback() is None


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

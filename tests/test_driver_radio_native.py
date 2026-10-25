#!/usr/bin/env python3
"""Native lifecycle uses fake devices and owned-process adapters, never a microphone."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_native as native
import driver_radio_access as access
from test_driver_radio_service import Fixture


class Worker:
    def __init__(self):self.events=[];self.closed=False;self.dead=False
    def poll(self):return 1 if self.dead else None
    def read(self):events=self.events;self.events=[];return events
    def close(self):self.closed=True


class Player:
    def __init__(self):self.closed=False;self.done=False
    def poll(self):return 0 if self.done else None
    def close(self):self.closed=True


class Harness:
    def __init__(self,d):
        self.f=Fixture(d);self.f.poll();self.workers=[];self.players=[];self.args=[]
        def worker(config):
            self.args.append(config);w=Worker();self.workers.append(w);return w
        def player(audio,volume):
            self.args.append(volume);p=Player();self.players.append(p);return p
        self.n=native.Supervisor(self.f.service,Path(d)/'machine',microphone=lambda:'Machine Mic',
                                 worker=worker,player=player,clock=lambda:self.f.at,ready=lambda:True)
    def control(self,**fields):
        state=self.n.status()
        return self.n.control(dict(binding=state['binding'],expected=state['revision'],**fields))


def t_enabled_startup_pause_resume_and_device_override_do_not_depend_on_views():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step();assert len(h.workers)==1 and h.args[-1]['microphone']=='Machine Mic'
        h.control(paused=True);h.n.step();assert h.workers[-1].closed and h.n.status()['phase']=='paused'
        h.control(paused=False);h.n.step();assert len(h.workers)==2
        h.f.access.update({'expected':1,'voice':dict(enabled=True,microphone='Engineer Mic',input_language='de')})
        h.n.step();assert h.workers[1].closed and h.args[-1]['microphone']=='Engineer Mic'
        assert h.args[-1]['language']=='de'
        h.f.access.update({'expected':2,'voice':dict(enabled=False,microphone=None,input_language='en')})
        h.n.step();assert h.workers[-1].closed and h.n.status()['phase']=='disabled'
        h.n.close()


def t_native_questions_speak_once_and_capture_is_stopped_during_output():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step();h.workers[0].events=[{'event':'question','text':'Fuel status?'}]
        h.n.step();assert h.workers[0].closed and h.f.service.pending_count()==1
        message=h.f.service.history()['messages'][-1]
        assert message['question']=='Fuel status?'
        h.f.service.attach(message['id'],message['epoch'],{'audio':b'fixture audio','audio_state':'ready'})
        h.n.step();assert len(h.players)==1
        for _ in range(3):h.n.step()
        assert len(h.players)==1 and len(h.workers)==1
        h.players[0].done=True;h.n.step();h.f.at+=1;h.n.step()
        assert len(h.workers)==2 and h.f.service.history()['messages'][-1]['state']=='spoken'
        h.n.close()


def t_revocation_expiry_mute_and_shutdown_stop_owned_output_before_reuse():
    for action in ('revocation','expiry','mute','close'):
        with tempfile.TemporaryDirectory() as d:
            h=Harness(d);h.n.step();message=h.f.request('pace')
            h.f.service.attach(message['id'],message['epoch'],{'audio':b'fixture audio','audio_state':'ready'})
            h.n.step();assert len(h.players)==1 and h.workers[0].closed
            if action=='revocation':h.f.access.update({'expected':1,'enabled':False})
            elif action=='expiry':h.f.at+=31
            elif action=='mute':h.control(muted=True)
            else:h.n.close()
            h.n.step();assert h.players[0].closed
            assert h.f.service.audio_for('unknown') is None
            h.n.close()


def t_worker_failure_retries_are_bounded_and_controls_are_revisioned():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step();h.workers[0].dead=True;h.n.step()
        assert h.n.status()['phase']=='error'
        for _ in range(5):h.n.step()
        assert len(h.workers)==1
        h.f.at+=6;h.n.step();assert len(h.workers)==2
        for change in ({'volume':True},{'volume':2},{'paused':'yes'},{'input_language':'fr'},{'arbitrary_path':'x'}):
            try:h.control(**change)
            except access.RadioError as e:assert e.code=='invalid_body'
            else:raise AssertionError('Invalid native control accepted')
        state=h.n.status();h.control(volume=.3)
        try:h.n.control(dict(binding=state['binding'],expected=state['revision'],paused=True))
        except access.RadioError as e:assert e.code=='conflict'
        else:raise AssertionError('Stale native control accepted')
        h.n.close()


def t_cleanup_failure_retains_the_native_slot_and_controls_do_not_block_on_process_io():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step();message=h.f.request('pace')
        h.f.service.attach(message['id'],message['epoch'],{'audio':b'fixture audio','audio_state':'ready'})
        h.n.step();player=h.players[0]
        def failed():raise ValueError('fixture cleanup failure')
        player.close=failed
        h.control(paused=True)  # HTTP controls only queue the change; no process I/O.
        try:h.n.step()
        except ValueError:pass  # The unsafe input or failed cleanup must be rejected.
        else:raise AssertionError('Cleanup failure hidden')
        assert h.f.service.take_playback() is None and h.n._ticket is not None
        player.close=lambda:None;h.n.step();assert h.n._ticket is None
        h.n.close()


def t_unreadable_settings_fail_closed_while_listening_or_speaking():
    import threading
    for speaking in (False,True):
        with tempfile.TemporaryDirectory() as d:
            h=Harness(d);h.n.step()
            if speaking:
                message=h.f.request('pace')
                h.f.service.attach(message['id'],message['epoch'],{'audio':b'fixture audio','audio_state':'ready'})
                h.n.step()
            h.f.access.path.write_text('{invalid settings')
            h.n.start()
            deadline=threading.Event()
            # Poll only the status; a missing valid settings document cannot admit work.
            for _ in range(100):
                if h.n.status()['phase']=='error':break
                deadline.wait(.01)
            assert h.n.status()['phase']=='error' and not h.n.status()['enabled']
            assert h.workers[0].closed
            if speaking:assert h.players[0].closed
            h.n.close()


def t_disabled_startup_never_opens_a_microphone_or_player():
    for field in ('profile','voice'):
        with tempfile.TemporaryDirectory() as d:
            h=Harness(d)
            body={'expected':1,'enabled':False} if field=='profile' else {
                'expected':1,'voice':{'enabled':False,'microphone':None,'input_language':'en'}}
            h.f.access.update(body)
            for _ in range(3):h.n.step()
            assert h.workers==[] and h.players==[] and h.n.status()['phase']=='disabled'
            h.n.close()


def t_setup_corrections_discard_a_question_captured_under_the_old_radio_epoch():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step();old=h.workers[0]
        old.events=[{'event':'question','text':'And one more lap?'}]
        binding=h.f.service.refresh()['binding'];state=h.f.session.read(binding)
        h.f.session.update(binding,dict(binding=binding,expected=state['revision'],setup={'laps':24}))
        h.f.poll();h.n.step()
        assert old.closed and len(h.workers)==2
        assert not any(m['kind']=='question' for m in h.f.service.history()['messages'])
        h.n.close()


def t_stalled_transcription_is_cancelled_at_its_own_short_deadline():
    with tempfile.TemporaryDirectory() as d:
        h=Harness(d);h.n.step();worker=h.workers[0]
        worker.events=[{'event':'transcribing'}];h.n.step()
        h.f.at+=9;h.n.step()
        assert worker.closed and h.n.status()['error']=='transcription_timed_out'
        assert not h.f.service.history()['messages']
        h.n.close()


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

#!/usr/bin/env python3
"""Cover encoders cannot accumulate empty-ring pumps across restarts."""
import io
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).parent))
from test_fanout import m


class Encoder:
    def __init__(self,dead=False):self.dead=dead
    def poll(self):return 0 if self.dead else None
    def kill(self):self.dead=True
    def wait(self,timeout=None):self.dead=True;return 0


def tap(ring):
    return m.FeedCoverTap(ring,'unused-cover.jpg',SimpleNamespace(info=lambda *a:None,debug=lambda *a:None),respawn_s=0)


def t_exited_encoder_pump_cannot_keep_reading_an_empty_live_ring():
    def read(*args):raise AssertionError('Exited encoder retained its empty-ring stdin pump')
    row=tap(SimpleNamespace(closed=False,read=read,live_offset=lambda:0))
    row._proc=Encoder(dead=True);stream=io.BytesIO()
    row._feed_stdin(stream)
    assert stream.closed


def t_cancelled_generation_does_not_wait_for_a_still_running_encoder():
    def read(*args):raise AssertionError('Cancelled generation retained its stdin pump')
    row=tap(SimpleNamespace(closed=False,read=read,live_offset=lambda:0))
    generation=threading.Event();generation.set();stream=io.BytesIO()
    row._feed_stdin(stream,Encoder(),generation)
    assert stream.closed


def t_encoder_exit_during_ring_read_cannot_write_stale_bytes_to_its_pipe():
    proc=Encoder();writes=[]
    class Pipe(io.BytesIO):
        def write(self,raw):writes.append(raw);return len(raw)
    def read(*args):proc.dead=True;return b'old encoder bytes',0
    row=tap(SimpleNamespace(closed=False,read=read,live_offset=lambda:0))
    row._proc=Encoder()
    row._feed_stdin(Pipe(),proc,threading.Event())
    assert not writes,'Exited generation wrote data after a blocked ring read'


def generations(unjoinable=False):
    threads=[];encoders=[];streams=[];peak=[0]
    def retired_read(*args):raise AssertionError('Retired generation kept reading its empty ring')
    row=tap(SimpleNamespace(closed=False,read=retired_read,live_offset=lambda:0))
    class Pump:
        def __init__(self,target,args=(),**kwargs):self.target=target;self.args=args;self.alive=False;threads.append(self)
        def start(self):self.alive=True;peak[0]=max(peak[0],sum(t.alive for t in threads))
        def join(self,timeout=None):
            if not unjoinable:self.target(*self.args);self.alive=False
        def is_alive(self):return self.alive
    def spawn(_tap):
        if len(encoders)==4:raise AssertionError('An unretired pump allowed another encoder generation')
        proc=Encoder();encoders.append(proc);stdin=io.BytesIO();video=io.BytesIO();streams.extend([stdin,video])
        if len(encoders)==4:row._stop.set()
        return proc,stdin,video
    row._spawn=spawn
    with patch.object(m.threading,'Thread',Pump):row._run()
    return row,threads,encoders,streams,peak[0]


def t_each_encoder_eof_retires_its_pump_before_replacement():
    _,threads,encoders,streams,peak=generations()
    assert len(encoders)==4
    assert peak<=1,'Encoder restarts accumulated simultaneous stdin pumps'
    assert not any(t.is_alive() for t in threads)
    assert all(stream.closed for stream in streams)


def t_unjoinable_old_pump_stops_respawning_instead_of_abandoning_ownership():
    row,threads,encoders,_,_=generations(unjoinable=True)
    assert len(encoders)==1,'An unjoinable pump was replaced by another generation'
    assert row._stop.is_set() and len(threads)==1


def t_uncertain_encoder_cleanup_cannot_spawn_a_replacement():
    with patch.object(Encoder,'kill',side_effect=OSError('injected stop failure')):
        row,threads,encoders,_,_=generations()
    assert len(encoders)==1 and row._stop.is_set(),'Failed encoder cleanup admitted a replacement'


def t_repeated_empty_encoder_restarts_have_at_most_one_real_pump():
    lock=threading.Lock();active=[0];peak=[0];spawned=[0]
    def read(*args):time.sleep(.005);return b'',0
    row=tap(SimpleNamespace(closed=False,read=read,live_offset=lambda:0))
    original=row._feed_stdin
    def tracked(*args):
        with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
        try:original(*args)
        finally:
            with lock:active[0]-=1
    row._feed_stdin=tracked
    def spawn(_tap):
        spawned[0]+=1
        if spawned[0]==16:row._stop.set()
        return Encoder(),io.BytesIO(),io.BytesIO()
    row._spawn=spawn
    try:row._run()
    finally:
        row.stop()
        until=time.monotonic()+2
        while active[0] and time.monotonic()<until:time.sleep(.01)
    assert spawned[0]==16 and peak[0]<=1,'Repeated encoder EOFs grew surviving real pump threads'
    assert active[0]==0


if __name__=='__main__':
    for name,fn in sorted(globals().copy().items()):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

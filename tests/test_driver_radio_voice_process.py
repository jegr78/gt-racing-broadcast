#!/usr/bin/env python3
"""Owned private voice workers use real controlled fixtures, never audio devices."""
import json,sys,time
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import driver_radio_voice_process as p
import driver_radio_voice_config as config
import ai_jobs


def t_native_ipc_is_bounded_and_shutdown_owns_the_worker():
    child=p.Owned([sys.executable,'-c','import sys,time;sys.stdin.buffer.read();print(\'{"event":"listening"}\',flush=True);time.sleep(30)'],b'fixture',config.execution_env(),events=True)
    try:
        deadline=time.monotonic()+3;events=[]
        while time.monotonic()<deadline and not events:events=child.read();time.sleep(.01)
        assert events==[{'event':'listening'}] and child.poll() is None
    finally:child.close()
    assert child.closed and child.proc.poll() is not None
    child.close()
    flood=p.Owned([sys.executable,'-c','import sys,time;sys.stdin.buffer.read();print("x"*5000,flush=True);time.sleep(30)'],b'fixture',config.execution_env(),events=True)
    try:
        assert flood.failed.wait(3) and flood.poll()==1
    finally:flood.close()


def t_cleanup_failure_does_not_mark_an_owned_process_closed_or_release_it():
    # Every process primitive stays mocked in this failure path.
    owner=object.__new__(p.Owned)
    owner.proc=Mock(pid=12345);owner.tree=Mock();owner.closed=False;owner.threads=[]
    with patch.object(ai_jobs,'terminate_tree',side_effect=ai_jobs.JobError('cleanup_failed','fixture')) as stop:
        for _ in range(2):
            try:owner.close()
            except ai_jobs.JobError as e:assert e.code=='cleanup_failed'
            else:raise AssertionError('Cleanup failure hidden')
            assert not owner.closed
        assert stop.call_count==2


def t_private_voice_and_model_entries_skip_profile_bootstrap():
    import test_racecast
    import driver_radio_voice_worker as worker
    import driver_radio_models as models
    m=test_racecast.m
    with patch.object(m,'_bootstrap',side_effect=AssertionError('Private worker loaded a profile')),patch.object(worker,'main',return_value=0) as start:
        assert m.main(['_radio-voice-worker'])==0;start.assert_called_once_with()
    with patch.object(m,'_bootstrap',side_effect=AssertionError('Model check loaded a profile')),patch.object(models,'main',return_value=0) as check:
        assert m.main(['_radio-model-check','fixture-models'])==0;check.assert_called_once_with(['fixture-models'])


def t_private_voice_worker_masks_bad_requests_and_never_opens_a_device():
    import subprocess
    path=Path(__file__).resolve().parents[1]/'src/scripts/driver_radio_voice_worker.py'
    done=subprocess.run([sys.executable,str(path)],input=b'{"microphone":"private device"}\n',
                        capture_output=True,timeout=3)
    assert done.returncode==0
    assert json.loads(done.stdout)=={'event':'error','code':'invalid_request'}
    assert b'private device' not in done.stdout


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

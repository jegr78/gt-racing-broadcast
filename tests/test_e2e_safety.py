#!/usr/bin/env python3
"""Resource admission is fail-closed and inspection cannot become detached."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import e2e_safety as safety


def limits(root,**values):
    group=Path(root)/safety.UNIT;group.mkdir(exist_ok=True)
    for name,value in dict({'pids.max':'512','memory.max':str(2*1024**3),'cpu.max':'200000 100000'},**values).items():
        (group/name).write_text(value)
    return group


def t_local_linux_reexec_has_kernel_caps_and_never_places_environment_secrets_on_argv():
    calls=[]
    def run(command,**kw):calls.append((command,kw));return SimpleNamespace(returncode=73)
    with patch.object(safety.sys,'platform','linux'),patch.dict(safety.os.environ,{'GITHUB_ACTIONS':'','SECRET_TEST_VALUE':'private-fixture'}), \
         patch.object(safety,'current_group',return_value=Path('/outside')),patch.object(safety.subprocess,'run',run):
        code=safety.enter(__file__,['--visual'])
    command,kw=calls[0]
    assert code==73 and '--property=TasksMax=512' in command
    assert '--property=CPUQuota=200%' in command and '--property=MemoryMax=2147483648' in command
    assert '--property=RuntimeMaxSec=900' in command and '--property=KillMode=control-group' in command
    assert '--unit='+safety.UNIT in command
    assert not any('private-fixture' in arg for arg in command)
    assert json.loads(kw['input'])['SECRET_TEST_VALUE']=='private-fixture'


def t_missing_containment_does_not_fall_back_to_an_unbounded_run():
    with patch.object(safety.sys,'platform','linux'),patch.dict(safety.os.environ,{'GITHUB_ACTIONS':''}), \
         patch.object(safety,'current_group',return_value=Path('/outside')), \
         patch.object(safety.subprocess,'run',side_effect=FileNotFoundError('systemd-run unavailable')):
        try:safety.enter(__file__,[])
        except RuntimeError as error:assert 'no unbounded fallback' in str(error)
        else:raise AssertionError('Missing containment admitted an unbounded E2E run')


def t_verified_worker_is_admitted_but_any_weakened_kernel_limit_is_rejected():
    with tempfile.TemporaryDirectory() as root:
        group=limits(root)
        assert safety.limited(group)
        for name,value in [('pids.max','max'),('pids.max','513'),('memory.max','max'),('cpu.max','max 100000'),('cpu.max','300000 100000')]:
            limits(root,**{name:value})
            try:safety.limited(group)
            except RuntimeError:pass  # weakened kernel limits must fail closed
            else:raise AssertionError('Weakened '+name+' was accepted')
            limits(root)
        with patch.object(safety.sys,'platform','linux'),patch.dict(safety.os.environ,{'GITHUB_ACTIONS':''}), \
             patch.object(safety,'current_group',return_value=group), \
             patch.object(safety.subprocess,'run',side_effect=AssertionError('Already admitted worker recursed')):
            assert safety.enter(__file__,[]) is None


def t_inspection_lease_is_finite_and_waits_in_the_foreground():
    for value in ('0','-1','nan','inf','601'):
        try:safety.keep_seconds(value)
        except ValueError:pass  # an invalid lease must never become detached work
        else:raise AssertionError('Unbounded inspection lease accepted')
    now=[0.0]
    def advance(seconds):now[0]+=seconds
    with patch.object(safety.time,'monotonic',side_effect=lambda:now[0]),patch.object(safety.time,'sleep',advance):
        safety.inspect_for(.5)
    assert now[0]==.5


if __name__=='__main__':
    for name,fn in sorted(globals().copy().items()):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

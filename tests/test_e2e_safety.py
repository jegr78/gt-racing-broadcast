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
import e2e_child as child


def limits(root,**values):
    group=Path(root)/safety.UNIT;group.mkdir(exist_ok=True)
    for name,value in dict({'pids.max':'512','memory.max':str(2*1024**3),'cpu.max':'200000 100000'},**values).items():
        (group/name).write_text(value)
    return group


def t_local_linux_reexec_has_kernel_caps_and_never_places_environment_secrets_on_argv():
    calls=[]
    def run(command,**kw):calls.append((command,kw));return SimpleNamespace(returncode=73)
    with patch.object(safety.sys,'platform','linux'),patch.dict(safety.os.environ,{'GITHUB_ACTIONS':'','SECRET_TEST_VALUE':'private-fixture'}), \
         patch.object(safety,'owner_identity',return_value=(12345,'42')), \
         patch.object(safety,'current_group',return_value=Path('/outside')),patch.object(safety.subprocess,'run',run):
        code=safety.enter(__file__,['--visual'])
    assert calls,'Local Linux E2E escaped containment'
    command,kw=calls[0]
    assert code==73 and '--property=TasksMax=512' in command
    assert '--property=CPUQuota=200%' in command and '--property=MemoryMax=2147483648' in command
    assert '--property=RuntimeMaxSec=900' in command and '--property=KillMode=control-group' in command
    assert '--unit='+safety.UNIT in command
    assert not any('private-fixture' in arg for arg in command)
    assert json.loads(kw['input'])['SECRET_TEST_VALUE']=='private-fixture'


def t_missing_containment_does_not_fall_back_to_an_unbounded_run():
    with patch.object(safety.sys,'platform','linux'),patch.dict(safety.os.environ,{'GITHUB_ACTIONS':''}), \
         patch.object(safety,'owner_identity',return_value=(12345,'42')), \
         patch.object(safety,'current_group',return_value=Path('/outside')), \
         patch.object(safety.subprocess,'run',side_effect=FileNotFoundError('systemd-run unavailable')):
        try:safety.enter(__file__,[])
        except RuntimeError as error:assert 'no unbounded fallback' in str(error)
        else:raise AssertionError('Missing containment admitted an unbounded E2E run')


def t_verified_worker_is_admitted_but_any_weakened_kernel_limit_is_rejected():
    with tempfile.TemporaryDirectory() as root:
        group=limits(root)
        assert safety.limited(group)
        for name,value in [('pids.max','max'),('pids.max','513'),('memory.max','max'),('memory.max',str(2*1024**3+1)),('cpu.max','max 100000'),('cpu.max','300000 100000')]:
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


def controlled_watchdog(owner_gone):
    class Stop:
        def __init__(self):self.stopped=False
        def set(self):self.stopped=True
        def is_set(self):return self.stopped
        def wait(self,seconds):raise AssertionError('Dead owner was ignored' if owner_gone else 'Expired child kept waiting')
    class Thread:
        def __init__(self,target,**kw):self.target=target
        def start(self):
            if owner_gone:self.target()
    proc=SimpleNamespace(pid=12345,returncode=None,poll=lambda:None,wait=lambda **kw:0)
    tree=SimpleNamespace(attach=lambda p:None,close=lambda:None)
    times=iter([0,0 if owner_gone else 3])
    fake_os=SimpleNamespace(name='posix',getpgrp=lambda:999,killpg=lambda *args:None,read=lambda *args:b'')
    with patch.object(child,'os',fake_os),patch.object(child.threading,'Event',Stop), \
         patch.object(child.threading,'Thread',Thread),patch.object(child.signal,'signal'), \
         patch.object(child.sys,'stdin',SimpleNamespace(fileno=lambda:0)), \
         patch.object(child.subprocess,'Popen',return_value=proc), \
         patch.object(child.ai_jobs,'WindowsTree',return_value=tree), \
         patch.object(child.ai_jobs,'terminate_tree') as terminate, \
         patch.object(child.time,'monotonic',side_effect=lambda:next(times)):
        code=child.supervise(['synthetic-child'],seconds=2)
    assert code==124
    terminate.assert_called_once_with(proc,tree)


def t_expired_child_deadline_retires_ownership_before_another_wait():
    controlled_watchdog(False)


def t_owner_pipe_eof_retires_ownership_before_another_wait():
    controlled_watchdog(True)


def t_invalid_child_lifetime_is_rejected_before_spawning():
    with patch.object(child.subprocess,'Popen',side_effect=AssertionError('Invalid lifetime reached a process')):
        for seconds in (0,-1,901,float('nan'),float('inf')):
            try:child.supervise(['synthetic'],seconds)
            except ValueError:pass  # the independent child deadline is mandatory
            else:raise AssertionError('Invalid child deadline admitted')


def t_kernel_owner_watcher_uses_only_its_pinned_cgroup_descriptor():
    with patch.object(safety.select,'select',return_value=([101],[],[])) as select, \
         patch.object(safety.os,'close') as close,patch.object(safety.os,'write',return_value=1) as write:
        safety.watch_owner(101,202)
    select.assert_called_once_with([101],[],[],900)
    write.assert_called_once_with(202,b'1')
    assert [call.args[0] for call in close.call_args_list]==[101,202]


def t_admission_owner_identity_and_cgroup_are_checked_before_watcher_launch():
    real_path=Path
    with tempfile.TemporaryDirectory() as directory:
        root=real_path(directory);owner=root/'12345';owner.mkdir()
        (owner/'stat').write_text('12345 (test) '+' '.join(['S']+['0']*18+['42']))
        group=limits(root)
        def path(value):return root if value=='/proc' else real_path(value)
        with patch.object(safety,'Path',side_effect=path), \
             patch.object(safety.os,'getuid',return_value=owner.stat().st_uid,create=True) as uid, \
             patch.object(safety.os,'pidfd_open',return_value=101,create=True), \
             patch.object(safety.os,'open',return_value=202) as opened,patch.object(safety.os,'close'), \
             patch.object(safety,'current_group',return_value=group) as current, \
             patch.object(safety.subprocess,'Popen') as launch:
            try:safety.owner_watchdog(12345,'changed')
            except RuntimeError:pass  # a reused owner identity cannot admit work
            else:raise AssertionError('Changed admission owner was accepted')
            opened.assert_not_called();launch.assert_not_called()
            uid.return_value=owner.stat().st_uid+1
            try:safety.owner_watchdog(12345,'42')
            except RuntimeError:pass  # another user's process cannot become this test's owner
            else:raise AssertionError('Foreign admission owner was accepted')
            uid.return_value=owner.stat().st_uid;current.return_value=root
            try:safety.owner_watchdog(12345,'42')
            except RuntimeError:pass  # the watcher may only control its verified test cgroup
            else:raise AssertionError('Uncontained owner watcher was accepted')
            opened.assert_not_called();launch.assert_not_called();current.return_value=group
            safety.owner_watchdog(12345,'42')
            opened.assert_called_once_with(group/'cgroup.kill',safety.os.O_WRONLY)
            assert launch.call_args.kwargs['pass_fds']==(101,202)


def t_invalid_owner_id_cannot_reach_a_kernel_pidfd():
    with patch.object(safety.os,'pidfd_open',side_effect=AssertionError('Invalid owner reached pidfd_open'),create=True):
        for pid in (0,1,-1,True,object()):
            try:safety.owner_watchdog(pid,'42')
            except RuntimeError:pass  # never coerce owner process identities
            else:raise AssertionError('Unsafe admission owner ID was accepted')


def t_self_hosted_actions_cannot_bypass_local_kernel_limits():
    for kind in ('self-hosted',''):
        with patch.object(safety.sys,'platform','linux'), \
             patch.dict(safety.os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':kind}), \
             patch.object(safety,'current_group',return_value=Path('/outside')), \
             patch.object(safety,'owner_identity',return_value=(12345,'42')), \
             patch.object(safety.subprocess,'run',return_value=SimpleNamespace(returncode=73)) as run:
            code=safety.enter(__file__,[])
        assert code==73 and run.called,'A self-hosted runner bypassed local containment'


def t_disposable_hosted_runner_uses_its_ci_job_boundary():
    with patch.object(safety.sys,'platform','linux'), \
         patch.dict(safety.os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}), \
         patch.object(safety,'current_group',side_effect=AssertionError('Hosted runner needs no user-manager dependency')):
        assert safety.enter(__file__,[]) is None


if __name__=='__main__':
    for name,fn in sorted(globals().copy().items()):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

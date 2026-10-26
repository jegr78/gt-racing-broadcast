"""Fail-closed local Linux containment and finite foreground inspection leases."""
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import time

UNIT='racecast-e2e.service'
MAX_SECONDS=900
MAX_TASKS=512
MAX_MEMORY=2*1024**3


def current_group():
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):
            relative=Path(line[3:].lstrip('/'))
            if '..' not in relative.parts:return Path('/sys/fs/cgroup')/relative
    raise RuntimeError('E2E requires cgroup v2 containment on local Linux')


def limited(group):
    if group.name!=UNIT:return False
    try:
        tasks=int((group/'pids.max').read_text())
        memory=int((group/'memory.max').read_text())
        quota,period=map(int,(group/'cpu.max').read_text().split())
    except (OSError,ValueError) as error:
        raise RuntimeError('E2E resource limits could not be verified') from error
    if not (0<tasks<=MAX_TASKS and 0<memory<=MAX_MEMORY and 0<quota<=2*period and period>0):
        raise RuntimeError('E2E cgroup resource limits are missing or weakened')
    return True


def owner_identity():
    return os.getpid(),Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]


def watch_owner(fd,kill_fd):
    try:select.select([fd],[],[],MAX_SECONDS)
    finally:
        os.close(fd)
        # This descriptor was opened inside the verified test cgroup. It cannot
        # retarget a newer unit or an unrelated PID when ownership changes.
        try:os.write(kill_fd,b'1')
        finally:os.close(kill_fd)


def owner_watchdog(pid,start):
    if type(pid) is not int or pid<=1:raise RuntimeError('Invalid E2E admission owner')
    path=Path('/proc')/str(pid)
    fd=os.pidfd_open(pid)
    kill_fd=None
    try:
        if path.stat().st_uid!=os.getuid() or path.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]!=start:
            raise RuntimeError('E2E admission owner changed')
        group=current_group()
        if not limited(group):raise RuntimeError('E2E owner watchdog requires verified containment')
        kill_fd=os.open(group/'cgroup.kill',os.O_WRONLY)
        subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--watch-owner',str(fd),str(kill_fd)],
                         pass_fds=(fd,kill_fd),stdin=subprocess.DEVNULL)
    finally:
        os.close(fd)
        if kill_fd is not None:os.close(kill_fd)


def enter(script,argv):
    """Return the contained run's exit code, or None inside an admitted worker."""
    if sys.platform!='linux':return None
    if os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted':return None
    if limited(current_group()):return None
    pid,start=owner_identity()
    command=['systemd-run','--user','--quiet','--wait','--collect','--pipe',
             '--unit='+UNIT,'--property=CPUQuota=200%',
             '--property=TasksMax='+str(MAX_TASKS),'--property=MemoryMax='+str(MAX_MEMORY),
             '--property=RuntimeMaxSec='+str(MAX_SECONDS),'--property=TimeoutStopSec=10s',
             '--property=KillMode=control-group','--property=CPUWeight=10',
             '--working-directory='+str(Path(script).resolve().parents[1]),
             sys.executable,str(Path(__file__).resolve()),'--exec',str(pid),start,str(Path(script).resolve()),*argv]
    # Forward the environment through stdin, never argv (which can expose secrets).
    try:
        result=subprocess.run(command,input=json.dumps(dict(os.environ))+'\n',text=True)
    except OSError as error:
        raise RuntimeError('E2E containment unavailable; no unbounded fallback was started') from error
    return result.returncode


def keep_seconds(value):
    seconds=float(value)
    if not math.isfinite(seconds) or not 0<seconds<=600:
        raise ValueError('Keep lease must be between 0 and 600 seconds')
    return seconds


def inspect_for(seconds):
    seconds=keep_seconds(seconds)
    print(f'Inspection remains supervised for at most {seconds:g}s; Ctrl+C cleans every owned service.',flush=True)
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:time.sleep(min(.2,max(0,deadline-time.monotonic())))


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--watch-owner':
        watch_owner(int(sys.argv[2]),int(sys.argv[3]));raise SystemExit(0)
    if len(sys.argv)<5 or sys.argv[1]!='--exec':raise SystemExit('Internal bounded E2E entrypoint')
    raw=sys.stdin.buffer.readline(256*1024)
    environment=json.loads(raw)
    if not isinstance(environment,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in environment.items()):
        raise SystemExit('Invalid bounded E2E environment')
    owner_watchdog(int(sys.argv[2]),sys.argv[3])
    os.execve(sys.executable,[sys.executable,*sys.argv[4:]],environment)

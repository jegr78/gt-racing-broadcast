"""Fail-closed local Linux containment and finite foreground inspection leases."""
import json
import math
import os
from pathlib import Path
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


def enter(script,argv):
    """Return the contained run's exit code, or None inside an admitted worker."""
    if sys.platform!='linux' or os.environ.get('GITHUB_ACTIONS')=='true':return None
    if limited(current_group()):return None
    command=['systemd-run','--user','--quiet','--wait','--collect','--pipe',
             '--unit='+UNIT,'--property=CPUQuota=200%',
             '--property=TasksMax='+str(MAX_TASKS),'--property=MemoryMax='+str(MAX_MEMORY),
             '--property=RuntimeMaxSec='+str(MAX_SECONDS),'--property=TimeoutStopSec=10s',
             '--property=KillMode=control-group','--property=CPUWeight=10',
             '--working-directory='+str(Path(script).resolve().parents[1]),
             sys.executable,str(Path(__file__).resolve()),'--exec',str(Path(script).resolve()),*argv]
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
    if len(sys.argv)<3 or sys.argv[1]!='--exec':raise SystemExit('Internal bounded E2E entrypoint')
    raw=sys.stdin.buffer.readline(256*1024)
    environment=json.loads(raw)
    if not isinstance(environment,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in environment.items()):
        raise SystemExit('Invalid bounded E2E environment')
    os.execve(sys.executable,[sys.executable,*sys.argv[2:]],environment)

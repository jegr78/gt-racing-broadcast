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
# The event simulation's longest scenario (120 min) plus 10 min to start, stop and report.
LONG_MAX_SECONDS=7800
MAX_TASKS=512
MAX_MEMORY=2*1024**3
# Host memory admission: a run started while the host swaps stalls on IO.
PROC=Path('/proc')
MEMORY_MARGIN=512*1024**2        # MemAvailable must hold the unit's cap plus this
PSI_SOME_AVG10_MAX=20.0          # % of the last 10 s some task waited for memory
PSI_FULL_AVG10_MAX=5.0           # % of the last 10 s every task waited for memory
PRESSURE_WAIT_SECONDS=120
PRESSURE_POLL_SECONDS=5


def current_group():
    for line in Path('/proc/self/cgroup').read_text(encoding='utf8',errors='replace').splitlines():
        if line.startswith('0::'):
            relative=Path(line[3:].lstrip('/'))
            if '..' not in relative.parts:return Path('/sys/fs/cgroup')/relative
    raise RuntimeError('E2E requires cgroup v2 containment on local Linux')


def limited(group):
    if group.name!=UNIT:return False
    try:
        tasks=int((group/'pids.max').read_text(encoding='utf8',errors='replace'))
        memory=int((group/'memory.max').read_text(encoding='utf8',errors='replace'))
        quota,period=map(int,(group/'cpu.max').read_text(encoding='utf8',errors='replace').split())
    except (OSError,ValueError) as error:
        raise RuntimeError('E2E resource limits could not be verified') from error
    if not (0<tasks<=MAX_TASKS and 0<memory<=MAX_MEMORY and 0<quota<=2*period and period>0):
        raise RuntimeError('E2E cgroup resource limits are missing or weakened')
    return True


def host_memory(proc=None):
    """{some, full, available}: the memory PSI avg10 values and MemAvailable in bytes;
    None for what this kernel does not offer."""
    proc=Path(PROC if proc is None else proc);state={'some':None,'full':None,'available':None}
    try:
        for line in (proc/'pressure'/'memory').read_text(encoding='utf8',errors='replace').splitlines():
            kind,*fields=line.split()
            values=dict(f.split('=',1) for f in fields if '=' in f)
            if kind in ('some','full') and 'avg10' in values:state[kind]=float(values['avg10'])
    except (OSError,ValueError):pass  # no PSI on this kernel: MemAvailable alone decides
    try:
        for line in (proc/'meminfo').read_text(encoding='utf8',errors='replace').splitlines():
            if line.startswith('MemAvailable:'):state['available']=int(line.split()[1])*1024
    except (OSError,ValueError,IndexError):pass  # no meminfo: PSI alone decides
    return state


def memory_strain(state):
    """Why the host cannot take a run now, or ''. A missing value never blocks."""
    reasons=[]
    if state['available'] is not None and state['available']<MAX_MEMORY+MEMORY_MARGIN:
        reasons.append(f"MemAvailable {state['available']//1024**2} MiB < {(MAX_MEMORY+MEMORY_MARGIN)//1024**2} MiB")
    if state['some'] is not None and state['some']>PSI_SOME_AVG10_MAX:
        reasons.append(f"memory PSI some avg10 {state['some']:g}% > {PSI_SOME_AVG10_MAX:g}%")
    if state['full'] is not None and state['full']>PSI_FULL_AVG10_MAX:
        reasons.append(f"memory PSI full avg10 {state['full']:g}% > {PSI_FULL_AVG10_MAX:g}%")
    return ', '.join(reasons)


def wait_for_memory(proc=None,clock=time.monotonic,sleep=time.sleep,seconds=PRESSURE_WAIT_SECONDS):
    """Return once the host has memory for a run; after `seconds` of pressure raise
    RuntimeError('host under memory pressure: ...') with the values. Never starts silently."""
    begin=clock();told=False
    while True:
        reason=memory_strain(host_memory(proc))
        if not reason:return
        if clock()-begin>=seconds:
            raise RuntimeError(f'host under memory pressure: {reason} (waited {seconds:g}s)')
        if not told:
            print(f'E2E admission: host under memory pressure ({reason}); waiting up to {seconds:g}s',file=sys.stderr,flush=True)
            told=True
        sleep(PRESSURE_POLL_SECONDS)


def owner_identity():
    return os.getpid(),Path('/proc/self/stat').read_text(encoding='utf8',errors='replace').rsplit(')',1)[1].split()[19]


def lifetime(seconds,long=False):
    """`seconds` as a whole lifetime; above 900 s only for a caller that opts in (`long`)."""
    cap=LONG_MAX_SECONDS if long else 900
    if not math.isfinite(seconds) or not 0<seconds<=cap:
        raise ValueError(f'E2E lifetime must be between 0 and {cap} seconds')
    return int(seconds)


def watch_owner(fd,kill_fd,seconds=None):
    try:select.select([fd],[],[],MAX_SECONDS if seconds is None else seconds)
    finally:
        os.close(fd)
        # This descriptor was opened inside the verified test cgroup. It cannot
        # retarget a newer unit or an unrelated PID when ownership changes.
        try:os.write(kill_fd,b'1')
        finally:os.close(kill_fd)


def owner_watchdog(pid,start,seconds=None):
    if type(pid) is not int or pid<=1:raise RuntimeError('Invalid E2E admission owner')
    path=Path('/proc')/str(pid)
    fd=os.pidfd_open(pid)
    kill_fd=None
    try:
        if path.stat().st_uid!=os.getuid() or path.joinpath('stat').read_text(encoding='utf8',errors='replace').rsplit(')',1)[1].split()[19]!=start:
            raise RuntimeError('E2E admission owner changed')
        group=current_group()
        if not limited(group):raise RuntimeError('E2E owner watchdog requires verified containment')
        kill_fd=os.open(group/'cgroup.kill',os.O_WRONLY)
        subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--watch-owner',str(fd),str(kill_fd),str(lifetime(MAX_SECONDS if seconds is None else seconds,long=True))],
                         pass_fds=(fd,kill_fd),stdin=subprocess.DEVNULL)
    finally:
        os.close(fd)
        if kill_fd is not None:os.close(kill_fd)


def enter(script,argv,seconds=None,long=False):
    """Return the contained run's exit code, or None inside an admitted worker.
    `seconds` (default MAX_SECONDS, read now) bounds the unit and the owner watcher;
    more than 900 s only with `long` (the event simulation)."""
    seconds=lifetime(MAX_SECONDS if seconds is None else seconds,long)
    if sys.platform!='linux':return None
    if os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted':return None
    if limited(current_group()):return None
    wait_for_memory()
    pid,start=owner_identity()
    command=['systemd-run','--user','--quiet','--wait','--collect','--pipe',
             '--unit='+UNIT,'--property=CPUQuota=200%',
             '--property=TasksMax='+str(MAX_TASKS),'--property=MemoryMax='+str(MAX_MEMORY),
             '--property=RuntimeMaxSec='+str(seconds),'--property=TimeoutStopSec=10s',
             '--property=KillMode=control-group','--property=CPUWeight=10',
             '--working-directory='+str(Path(script).resolve().parents[1]),
             sys.executable,str(Path(__file__).resolve()),'--exec',str(pid),start,str(seconds),str(Path(script).resolve()),*argv]
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


def main(argv):
    """The internal entrypoints; `enter` already capped their lifetime, which may be long."""
    if len(argv)==5 and argv[1]=='--watch-owner':
        watch_owner(int(argv[2]),int(argv[3]),lifetime(int(argv[4]),long=True));return
    if len(argv)<6 or argv[1]!='--exec':raise SystemExit('Internal bounded E2E entrypoint')
    raw=sys.stdin.buffer.readline(256*1024)
    environment=json.loads(raw)
    if not isinstance(environment,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in environment.items()):
        raise SystemExit('Invalid bounded E2E environment')
    owner_watchdog(int(argv[2]),argv[3],lifetime(int(argv[4]),long=True))
    os.execve(sys.executable,[sys.executable,*argv[5:]],environment)


if __name__=='__main__':
    main(sys.argv)

"""Independent E2E child owner: control-pipe EOF or a deadline kills its own tree."""
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/scripts'))
import ai_jobs


def supervise(argv,seconds=900,limit=900):
    if not math.isfinite(seconds) or not 0<seconds<=limit:raise ValueError('Invalid E2E child lifetime')
    stopped=threading.Event()
    def stop(_signal,_frame):stopped.set()
    for name in ('SIGTERM','SIGINT','SIGHUP'):
        if hasattr(signal,name):signal.signal(getattr(signal,name),stop)
    def owner_closed():
        try:
            # Do not hold Python's buffered-stdin lock in a daemon at shutdown.
            while os.read(sys.stdin.fileno(),1):pass
        finally:stopped.set()
    threading.Thread(target=owner_closed,daemon=True).start()
    tree=ai_jobs.WindowsTree();proc=None
    try:
        flags={'creationflags':0x08000004} if os.name=='nt' else {'start_new_session':True}
        proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,**flags)
        tree.attach(proc)
        deadline=time.monotonic()+seconds
        while proc.poll() is None and not stopped.is_set() and time.monotonic()<deadline:
            stopped.wait(.1)
        return proc.returncode if proc.returncode is not None else 124
    finally:
        if proc is not None:
            if os.name!='nt' and proc.poll() is None:
                if type(proc.pid) is not int or proc.pid<=1 or proc.pid==os.getpgrp():
                    raise RuntimeError('Refusing an unsafe E2E child identity')
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass  # the owned child group already exited
                try:proc.wait(timeout=2)
                except subprocess.TimeoutExpired:pass  # the owned-tree kill below enforces teardown
            ai_jobs.terminate_tree(proc,tree)
        else:tree.close()


def parse_cli(argv):
    """`SECONDS [--long] -- COMMAND...` -> (seconds, limit, command). Only `--long` (the
    event simulation) lifts the 900 s cap to e2e_safety.LONG_MAX_SECONDS."""
    long=len(argv)>1 and argv[1]=='--long'
    rest=argv[2:] if long else argv[1:]
    if not argv or not rest or rest[0]!='--' or len(rest)<2:raise ValueError('Expected lifetime [--long] -- command')
    import e2e_safety
    return float(argv[0]),e2e_safety.LONG_MAX_SECONDS if long else 900,rest[1:]


if __name__=='__main__':
    try:seconds,limit,command=parse_cli(sys.argv[1:])
    except ValueError as error:raise SystemExit(str(error)) from None
    raise SystemExit(supervise(command,seconds,limit=limit))

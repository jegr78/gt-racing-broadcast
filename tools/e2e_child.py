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


def supervise(argv,seconds=900):
    if not math.isfinite(seconds) or not 0<seconds<=900:raise ValueError('Invalid E2E child lifetime')
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


if __name__=='__main__':
    if len(sys.argv)<4 or sys.argv[2]!='--':raise SystemExit('Expected lifetime -- command')
    raise SystemExit(supervise(sys.argv[3:],float(sys.argv[1])))

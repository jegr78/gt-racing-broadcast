"""Owned long-lived native workers and players with bounded private IPC."""
import json
import os
import queue
import subprocess
import threading

import ai_jobs
from driver_radio_process import ProviderError


class Owned:
    def __init__(self,argv,payload,env,events=False):
        self.proc=None;self.tree=None;self.closed=False
        self.failed=threading.Event();self.events=queue.Queue(maxsize=8)
        self.threads=[]
        try:
            self.tree=ai_jobs.WindowsTree()
            flags={'creationflags':0x08000204} if os.name=='nt' else {'start_new_session':True}
            self.proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE if events else subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,env=env,**flags)
            self.tree.attach(self.proc)
            def write():
                try:
                    with self.proc.stdin as out:out.write(payload)
                except OSError:self.failed.set()
            thread=threading.Thread(target=write,daemon=True);self.threads.append(thread);thread.start()
            if events:
                def read():
                    try:
                        while line:=self.proc.stdout.readline(4097):
                            if len(line)>4096:raise ValueError('Oversized native event')
                            event=json.loads(line)
                            if not isinstance(event,dict):raise ValueError('Invalid native event')
                            self.events.put_nowait(event)
                    except (OSError,ValueError,RecursionError,queue.Full):self.failed.set()
                thread=threading.Thread(target=read,daemon=True);self.threads.append(thread);thread.start()
        except Exception:
            self.close();raise

    def read(self):
        result=[]
        for _ in range(8):
            try:result.append(self.events.get_nowait())
            except queue.Empty:break
        return result

    def poll(self):
        return 1 if self.failed.is_set() else self.proc.poll()

    def close(self):
        if self.closed:return
        if self.proc is not None:ai_jobs.terminate_tree(self.proc,self.tree)
        elif self.tree is not None:self.tree.close()
        for thread in self.threads:thread.join(1)
        if any(thread.is_alive() for thread in self.threads):raise ProviderError('cleanup_failed')
        if self.proc is not None:
            for stream in (self.proc.stdin,self.proc.stdout):
                if stream is not None:stream.close()
        self.closed=True

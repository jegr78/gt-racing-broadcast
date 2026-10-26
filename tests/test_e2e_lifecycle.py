#!/usr/bin/env python3
"""Synthetic harness process-tree cleanup and isolated relay sockets."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('e2e_harness', ROOT / 'tools/e2e.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def t_windows_teardown_kills_the_tree_before_waiting_and_closes_the_log():
    calls = []
    proc = mock.Mock(pid=12345)
    proc.poll.return_value = None
    proc.wait.side_effect = lambda **kw: calls.append('wait')
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)
    with mock.patch.object(harness, 'os', SimpleNamespace(name='nt')), mock.patch.object(harness.subprocess, 'run', run):
        harness._kill(proc)
    assert calls[0] == ['taskkill', '/PID', '12345', '/T', '/F'], calls
    assert calls[1] == 'wait', calls
    proc.terminate.assert_not_called()
    proc._logfh.close.assert_called_once()


def t_spawn_failure_closes_the_owned_log():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'child.log'
        opened = []
        real_open = open
        def tracked(*args, **kwargs):
            fh = real_open(*args, **kwargs)
            opened.append(fh)
            return fh
        with mock.patch('builtins.open', tracked), mock.patch.object(harness.subprocess, 'Popen', side_effect=OSError('spawn failed')):
            try:
                harness._spawn(['missing'], dict(os.environ), path)
            except OSError:
                pass  # The injected spawn failure must still reach the caller.
            else:
                raise AssertionError('spawn failure hidden')
        assert len(opened) == 1 and opened[0].closed, 'failed spawn leaked its log handle'


def t_keep_startup_failure_still_cleans_every_acquired_owner():
    with tempfile.TemporaryDirectory() as directory:
        target=Path(directory)/'run';target.mkdir()
        with mock.patch.object(harness.tempfile,'mkdtemp',return_value=str(target)), \
             mock.patch.object(harness,'_spawn',side_effect=RuntimeError('injected startup failure')), \
             mock.patch.object(harness,'_cleanup') as cleanup:
            try:harness.run_synthetic(SimpleNamespace(keep=True,timeout=1))
            except RuntimeError as error:assert str(error)=='injected startup failure'
            else:raise AssertionError('Startup failure was hidden')
            cleanup.assert_called_once_with([],[],str(target))


def listener(root):
    path=root/'listener.py'
    path.write_text('''import os,socket,sys,time
from pathlib import Path
s=socket.socket()
if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):s.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
s.bind(('127.0.0.1',0));s.listen()
target=Path(sys.argv[1]);temporary=target.with_suffix('.tmp')
temporary.write_text(str(s.getsockname()[1]));temporary.replace(target)
time.sleep(5)
''',encoding='utf8')
    return path


def wait_port_file(path,proc):
    deadline=time.monotonic()+5
    while not path.exists() and time.monotonic()<deadline:
        assert proc.poll() is None,'Controlled listener exited before binding'
        time.sleep(.01)
    assert path.exists(),'Controlled listener did not bind'
    return int(path.read_text())


def can_bind(port):
    try:
        with socket.socket() as sock:
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            sock.bind(('127.0.0.1',port))
        return True
    except OSError:return False


def t_independent_child_deadline_releases_its_listener():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);ready=root/'ready'
        with mock.patch.object(harness.e2e_safety,'MAX_SECONDS',2):
            proc=harness._spawn([sys.executable,str(listener(root)),str(ready)],dict(os.environ),root/'child.log')
        try:
            port=wait_port_file(ready,proc)
            code=proc.wait(timeout=5)
            assert code==124 and can_bind(port),'Independent deadline left its listener alive'
        finally:harness._kill(proc)


def t_owner_death_closes_child_control_pipe_and_releases_its_listener():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);ready=root/'ready';child=listener(root)
        owner=root/'owner.py'
        owner.write_text('''import os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import e2e
e2e.e2e_safety.MAX_SECONDS=3
proc=e2e._spawn([sys.executable,sys.argv[2],sys.argv[3]],dict(os.environ),sys.argv[4])
until=time.monotonic()+2
while not Path(sys.argv[3]).exists() and time.monotonic()<until:time.sleep(.01)
os._exit(0)
''',encoding='utf8')
        with (root/'owner.log').open('wb') as output:
            proc=subprocess.Popen([sys.executable,str(owner),str(ROOT/'tools'),str(child),str(ready),str(root/'child.log')],stdout=output,stderr=subprocess.STDOUT)
            try:
                port=wait_port_file(ready,proc);proc.wait(timeout=5)
                until=time.monotonic()+1.5
                while not can_bind(port) and time.monotonic()<until:time.sleep(.01)
                assert can_bind(port),'Dead E2E owner left its supervised listener alive'
            finally:
                proc.wait(timeout=5)
                # Both test listener and independent watchdog also self-expire.
                until=time.monotonic()+5
                while ready.exists() and not can_bind(int(ready.read_text())) and time.monotonic()<until:time.sleep(.02)


def t_unsafe_process_id_is_rejected_before_any_signal():
    for pid in (1,0,-1,True,mock.MagicMock()):
        proc=SimpleNamespace(pid=pid)
        with mock.patch.object(harness.os,'killpg',side_effect=AssertionError('Unsafe group signal'),create=True):
            try:harness._kill(proc)
            except RuntimeError:pass  # unsafe identities must fail before any signal
            else:raise AssertionError('Unsafe E2E identity was accepted')


def t_teardown_releases_a_stubborn_grandchild_socket():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        ready = root / 'port'
        child = root / 'child.py'
        child.write_text('''import os,signal,socket,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
ready=Path(sys.argv[1]);temporary=ready.with_suffix('.tmp')
temporary.write_text(str(s.getsockname()[1])+' '+str(os.getpid()));temporary.replace(ready)
while True:time.sleep(.1)
''', encoding='utf-8')
        parent = root / 'parent.py'
        parent.write_text('''import subprocess,sys,time
subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])
while True:time.sleep(.1)
''', encoding='utf-8')
        proc = harness._spawn([sys.executable, str(parent), str(child), str(ready)], dict(os.environ), root / 'parent.log')
        try:
            until = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < until:
                assert proc.poll() is None
                time.sleep(.02)
            assert ready.exists(), 'grandchild never bound its socket'
            port, child_pid = map(int, ready.read_text(encoding='utf-8').split())
            harness._kill(proc)
            until = time.monotonic() + 3
            while time.monotonic() < until:
                try:
                    with socket.socket() as probe:
                        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                        probe.bind(('127.0.0.1', port))
                    break
                except OSError:
                    # A descendant still owns the listener; wait for tree teardown.
                    pass
                time.sleep(.02)
            else:
                raise AssertionError('grandchild socket cannot be rebound after teardown')
            assert proc.poll() is not None and proc._logfh.closed
        finally:
            # The old implementation leaks this deliberately stubborn process.
            if os.name == 'posix':
                import signal
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # Correct teardown already removed the whole group.
            else:
                subprocess.run(['taskkill', '/PID', str(child_pid if ready.exists() else proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.wait(timeout=5)


def t_cleanup_attempts_every_owned_process_when_one_stop_fails():
    stopped = []
    def kill(proc):
        stopped.append(proc)
        if proc == 'bad':
            raise OSError('test failure')
    server = mock.Mock()
    with tempfile.TemporaryDirectory() as directory, mock.patch.object(harness, '_kill', kill):
        try:
            harness._cleanup(['first', 'bad', 'last'], [server], directory)
        except RuntimeError:
            pass  # Cleanup failure is explicit after the remaining owners were stopped.
        else:
            raise AssertionError('failed process-tree stop was hidden')
        assert stopped == ['last', 'bad', 'first'], stopped
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        assert Path(directory).exists(), 'failed cleanup removed its diagnostic logs'


def t_pov_ui_readiness_failure_stops_the_unreturned_child():
    child = mock.Mock()
    with tempfile.TemporaryDirectory() as home, \
         mock.patch.object(harness, '_pov_home', return_value=(['python'], home)), \
         mock.patch.object(harness, '_seed_telemetry'), \
         mock.patch.object(harness.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
         mock.patch.object(harness, '_spawn', return_value=child), \
         mock.patch.object(harness, '_wait_ready', side_effect=RuntimeError('not ready')), \
         mock.patch.object(harness, '_kill') as kill:
        try:
            harness._start_pov_ui(None, home, {}, 1)
        except RuntimeError as exc:
            assert str(exc) == 'not ready'
        else:
            raise AssertionError('readiness failure hidden')
        kill.assert_called_once_with(child)


def t_feed_port_selection_rejects_production_and_duplicate_ports():
    with mock.patch.object(harness.E, 'free_port', side_effect=[53001, 53002, 53003, 41001, 41001, 41002, 41003]):
        assert harness._free_feed_ports() == [41001, 41002, 41003]


def t_synthetic_relay_arguments_isolate_every_socket():
    args = harness._synthetic_relay_args()
    options = dict(zip(args[::2], args[1::2], strict=True))
    ports = [int(p) for p in options['--ports'].split(',')] + [int(options['--pov-port'])]
    assert len(set(ports)) == 3 and not {53001, 53002, 53003}.intersection(ports), ports
    assert options['--gt7-bind'] == '127.0.0.1' and options['--gt7-port'] == '0'
    assert options['--gt7-ps-ip'] == '127.0.0.1'
    sockets = []
    try:
        for port in ports:
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(('127.0.0.1', port))
    finally:
        for sock in sockets:
            sock.close()


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn()
            print('PASS', name)

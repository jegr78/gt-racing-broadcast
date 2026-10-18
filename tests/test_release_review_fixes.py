#!/usr/bin/env python3
"""Release review regressions with disposable captures and synthetic AI processes."""
import json
import contextlib
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
from unittest.mock import Mock, patch

import test_ai_http as http
import test_ai_jobs as jobs
import test_gt7_laps as laps
import test_racecast as archive
import test_telemetry_workspace as workspace
import test_ui_server as web
import ai_jobs
import gt7_recording
import gt7_telemetry


def t_opaque_origin_cannot_delete_or_assign_tracks_without_profile():
    calls = []
    ctx = web._ctx()
    ctx['telemetry_delete'] = lambda *args: calls.append(args) or {'ok': True}
    ctx['telemetry_learn'] = lambda *args, **kw: calls.append(args) or {'ok': True}
    server, port = web._serve(ctx)
    try:
        for endpoint, body in [('delete', {'rec': 'known-import'}),
                               ('learn', {'rec': 'latest', 'track_id': 'ring01'})]:
            for origin in ['null', 'https://foreign.example.test']:
                code, _ = http.request(port, '/api/telemetry/' + endpoint, body,
                                       {'Origin': origin, 'Content-Type': 'text/plain'})
                assert code == 403 and not calls
            for headers in [{}, {'Origin': f'http://127.0.0.1:{port}'}]:
                code, _ = http.request(port, '/api/telemetry/' + endpoint, body, headers)
                assert code == 200
            calls.clear()
    finally:
        server.shutdown()
        server.server_close()


def t_foreign_profile_cannot_cancel_another_profiles_job_over_http():
    with workspace.profiles() as root:
        m = workspace.m
        machine = Path(m._runtime_base_dir())
        runner = ai_jobs.Runner(machine, machine / 'alpha', 'alpha')
        owner = ai_jobs.BackgroundJobs()
        runner.background_jobs = owner
        adapter = jobs.FakeAdapter(root, 'tree')
        ctx = web._ctx()
        ctx.update(telemetry_profile_scope=m.telemetry_profile_scope,
                   ai_request=m.ai_request_data)
        server, port = web._serve(ctx)
        try:
            # Server startup must not consume the synthetic provider's timeout.
            job = runner.start(jobs.package(), jobs.prepared(root), adapter)
            jobs.wait_for(lambda: hasattr(adapter, 'scratch_output') and
                          (adapter.scratch_output / 'child.pid').exists())
            code, status = http.request(port, '/api/ai/status?profile=beta')
            assert code == 200 and status['active']['foreign'] and status['active']['id'] == job
            code, result = http.request(port, '/api/ai/cancel?profile=beta',
                                        {'profile': 'beta', 'id': job})
            assert code == 200 and result['cancelled'] is False
            assert not runner.cancel_path.exists()
            assert runner.status('alpha')['busy']
            code, result = http.request(port, '/api/ai/cancel?profile=alpha',
                                        {'profile': 'alpha', 'id': job})
            assert code == 200 and result['cancelled'] is True
        finally:
            owner.close()
            server.shutdown()
            server.server_close()


def replace_preserving_stat(path, raw, overwrite=False):
    before = os.stat(path)
    assert len(raw) == before.st_size
    if overwrite:
        Path(path).write_bytes(raw)
    else:
        Path(path + '.replacement').write_bytes(raw)
        os.replace(path + '.replacement', path)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


@contextlib.contextmanager
def windows_metadata(path):
    """Model Windows creation-time stat semantics and its separate native ChangeTime."""
    creation = os.stat(path).st_ctime_ns
    changed = [creation]
    def stat(candidate, *args, **kwargs):
        result = os.stat(candidate, *args, **kwargs)
        if os.fspath(candidate) != os.fspath(path):
            return result
        values = {key: getattr(result, key) for key in dir(result) if key.startswith('st_')}
        values.update(st_ctime_ns=creation, st_ctime=creation / 1e9)
        return types.SimpleNamespace(**values)
    modeled = types.SimpleNamespace(**vars(os))
    modeled.name, modeled.stat = 'nt', stat
    with patch.object(gt7_recording, 'os', modeled), patch.object(gt7_recording, '_windows_change_time', side_effect=lambda _path: changed[0]):
        yield changed


def t_windows_equal_stat_in_place_capture_overwrite_invalidates_both_caches():
    m = archive.m
    for clear_memory in [False, True]:
        with archive._telemetry_sandbox() as (rec_dir, fixture):
            path = fixture.write_circle_recording(rec_dir)
            rec = archive._stem(path)
            with windows_metadata(path) as changed:
                first = m.telemetry_lap_data(rec, '1', '3')['lap']
                reader = gt7_recording.Recording(path)
                raw = bytearray(Path(path).read_bytes())
                for _ts, _kind, _packet in reader.packets():
                    struct.pack_into('<i', raw, reader.record_start + gt7_recording._REC.size
                                     + gt7_telemetry.OFF_CAR_ID, fixture.CAR + 1)
                old_signature = gt7_recording.file_signature(path)
                replace_preserving_stat(path, raw, overwrite=True)
                changed[0] += 1000
                assert gt7_recording.file_signature(path)[:4] == old_signature[:4]
                if clear_memory:
                    m._TELEMETRY_MEMO.clear()
                assert m.telemetry_lap_data(rec, '1', '3', build=False).get('unindexed') == 1
                fresh = m.telemetry_lap_data(rec, '1', '3')['lap']
                assert fresh['car_id'] == first['car_id'] + 1


def t_native_windows_change_time_tracks_preserved_mtime_writes():
    if os.name != 'nt':
        return  # The modeled Windows semantics run on every platform above.
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / 'file.json'
        path.write_bytes(b'old')
        before = os.stat(path)
        signature = gt7_recording.file_signature(path)
        # FILETIME's 100 ns units do not imply a new kernel clock tick per write.
        time.sleep(.05)
        path.write_bytes(b'new')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        current = gt7_recording.file_signature(path)
        assert current[:4] == signature[:4], (signature, current)
        assert current[4] != signature[4], (signature, current)


def t_windows_metadata_query_closes_handle_on_success_and_error():
    import ctypes as ct
    def read_info(_handle, _kind, pointer, _size):
        pointer._obj.CreationTime = 7
        pointer._obj.ChangeTime = 123456
        return True
    kernel = types.SimpleNamespace(CreateFileW=Mock(return_value=17),
                                   GetFileInformationByHandleEx=Mock(side_effect=read_info),
                                   CloseHandle=Mock(return_value=True))
    with patch.object(ct, 'WinDLL', return_value=kernel, create=True), patch.object(ct, 'get_last_error', return_value=5, create=True), patch.object(ct, 'WinError', side_effect=lambda _code: OSError('metadata unavailable'), create=True):
        assert gt7_recording._windows_change_time('fixture') == 12345600
        kernel.CloseHandle.assert_called_once_with(17)
        kernel.CloseHandle.reset_mock()
        kernel.GetFileInformationByHandleEx.side_effect = None
        kernel.GetFileInformationByHandleEx.return_value = False
        try:
            gt7_recording._windows_change_time('fixture')
        except OSError as exc:
            assert 'metadata unavailable' in str(exc)
        else:
            raise AssertionError('metadata failure must not return a reusable signature')
        kernel.CloseHandle.assert_called_once_with(17)
        kernel.CloseHandle.reset_mock()
        kernel.CreateFileW.return_value = ct.c_void_p(-1).value
        try:
            gt7_recording._windows_change_time('fixture')
        except OSError as exc:
            assert 'metadata unavailable' in str(exc)
        else:
            raise AssertionError('an invalid handle must fail closed')
        kernel.CloseHandle.assert_not_called()


def t_replaced_recording_invalidates_memory_and_disk_indexes():
    m = archive.m
    for clear_memory in [False, True]:
        with archive._telemetry_sandbox() as (rec_dir, fixture):
            path = fixture.write_circle_recording(rec_dir)
            rec = archive._stem(path)
            first = m.telemetry_lap_data(rec, '1', '3')
            reader = gt7_recording.Recording(path)
            raw = bytearray(Path(path).read_bytes())
            ident = 'a' * 32
            raw[:reader.header_end] = raw[:reader.header_end].replace(
                reader.header['recording_id'].encode(), ident.encode())
            for _ts, _kind, _packet in reader.packets():
                struct.pack_into('<i', raw, reader.record_start + gt7_recording._REC.size
                                 + gt7_telemetry.OFF_CAR_ID, fixture.CAR + 1)
            replace_preserving_stat(path, raw)
            if clear_memory:
                m._TELEMETRY_MEMO.clear()
            stale = m.telemetry_lap_data(rec, '1', '3', build=False)
            assert stale.get('unindexed') == 1, stale
            fresh = m.telemetry_lap_data(rec, '1', '3')
            assert fresh['lap']['recording_id'] == ident
            assert fresh['lap']['car_id'] == first['lap']['car_id'] + 1


def t_same_inode_rewritten_capture_cannot_resume_old_laps():
    with tempfile.TemporaryDirectory() as root:
        path = laps.write_circle_recording(root)
        laps._index(path)
        # Preserve the tail check and started label, changing only the UUID.
        before = os.stat(path)
        reader = gt7_recording.Recording(path)
        raw = Path(path).read_bytes().replace(reader.header['recording_id'].encode(), b'b' * 32, 1)
        with open(path, 'wb') as stream:
            stream.write(raw)
            stream.write(gt7_recording._encode(1_700_000_100, ord('~'), laps._pkt(5, 50, 0)))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with laps._packet_starts() as starts:
            laps._index(path)
        assert starts[0] is None, 'a different recording header must force a cold replay'


def check_definition_replacement(supplied, overwrite=False):
    m = archive.m
    with archive._telemetry_sandbox() as (rec_dir, fixture), tempfile.TemporaryDirectory() as bundle:
        path = fixture.write_circle_recording(rec_dir)
        rec = archive._stem(path)
        m.telemetry_lap_data(rec, '1', '3')
        draft = m.telemetry_track_definition_read_data(rec, '1', '3')['definition']
        draft['reviewed'] = True
        draft['variants'][0].update(kind='coaching', reviewed=True)
        store = m._telemetry_definition_store()
        if supplied:
            store.bundled = bundle
            definition_path = os.path.join(bundle, os.path.basename(store.path(draft['layout_id'], draft['reverse'])))
            Path(definition_path).write_text(json.dumps(draft), encoding='utf-8')
        else:
            store.save(draft, 0)
            definition_path = store.path(draft['layout_id'], draft['reverse'])
        with patch.object(m, '_telemetry_definition_store', return_value=store), windows_metadata(definition_path) as changed:
            first = m.telemetry_lap_data(rec, '1', '3')['lap']
            raw = Path(definition_path).read_bytes()
            name = draft['variants'][0]['name']
            raw = raw.replace(json.dumps(name).encode(), json.dumps('X' * len(name)).encode(), 1)
            replace_preserving_stat(definition_path, raw, overwrite=overwrite)
            changed[0] += 1000
            warm = m.telemetry_lap_data(rec, '1', '3')['lap']
            assert warm['definition_fingerprint'] != first['definition_fingerprint']


def t_archive_definitions_detect_equal_stat_replacement():
    for supplied in [False, True]:
        for overwrite in [False, True]:
            check_definition_replacement(supplied, overwrite)


def child_service(root, interrupt=False):
    """Run the real UI lifecycle with an owned synthetic provider and ephemeral port."""
    m = workspace.m
    root = Path(root)
    adapter = jobs.FakeAdapter(root, 'tree')
    text = adapter.script.read_text(encoding='utf-8')
    adapter.script.write_text(text.replace('sys.stdin.read()',
        "sys.stdin.read();Path(__file__).with_name('provider.pid').write_text(str(os.getpid()))"), encoding='utf-8')
    with workspace.profiles():
        machine = Path(m._runtime_base_dir())
        runner = ai_jobs.Runner(machine, machine / 'alpha', 'alpha')
        prepared = jobs.prepared(root)
        serve = web.us.serve
        def ready(ctx, host, port):
            server = serve(ctx, host, port)
            # Start the timed fixture only once the real UI is ready to serve.
            job = runner.start(jobs.package(), prepared, adapter)
            jobs.wait_for(lambda: hasattr(adapter, 'scratch_output') and
                          (adapter.scratch_output / 'child.pid').exists())
            print('READY ' + json.dumps({'port': server.server_address[1], 'job': job,
                  'machine': str(machine), 'provider': int((root / 'provider.pid').read_text()),
                  'child': int((adapter.scratch_output / 'child.pid').read_text())}), flush=True)
            if interrupt:
                server.serve_forever = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
            return server
        with patch.object(web.us, 'ui_port', return_value=0), patch.object(web.us, 'probe_instance', return_value=None), patch.object(web.us, 'serve', side_effect=ready), patch.object(m, '_read_env_file', return_value={}):
            m.run_ui([], open_browser=False)
        state = json.loads(next((machine / 'alpha').glob('telemetry-analyses/*/*/run.json')).read_text(encoding='utf-8'))
        assert state['state'] == 'cancelled' and state['finished_at']


def alive(pid):
    if os.name == 'nt':
        import ctypes as ct
        kernel = ct.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ct.c_void_p
        kernel.WaitForSingleObject.argtypes = [ct.c_void_p, ct.c_uint32]
        kernel.CloseHandle.argtypes = [ct.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True,
                            text=True, errors='replace', timeout=5)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


def t_ui_quit_and_keyboard_interrupt_stop_owned_provider_descendants():
    for interrupt in [False, True]:
        with tempfile.TemporaryDirectory() as root:
            proc = subprocess.Popen([sys.executable, __file__, '--child', root, str(int(interrupt))],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding='utf-8', errors='replace')
            info = None
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        raise AssertionError(proc.stderr.read())
                    if line.startswith('READY '):
                        info = json.loads(line[6:])
                        break
                if not interrupt:
                    code, _ = http.request(info['port'], '/api/quit', {})
                    assert code == 409
                    assert alive(info['provider']) and alive(info['child'])
                    code, result = http.request(info['port'], '/api/quit', {'confirm': True})
                    assert code == 200 and result['ok']
                out, err = proc.communicate(timeout=25)
                assert proc.returncode == 0, out + err
                assert not alive(info['provider']) and not alive(info['child'])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=5)
                if info:
                    for pid in [info['provider'], info['child']]:
                        if alive(pid):
                            if os.name == 'nt':
                                subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            else:
                                os.kill(pid, signal.SIGKILL)


def t_background_shutdown_preserves_external_job_and_rejects_new_starts():
    with tempfile.TemporaryDirectory() as root:
        machine = Path(root) / 'machine'
        owner = ai_jobs.BackgroundJobs()
        lease = ai_jobs.Lease(machine).acquire()
        try:
            owner.close()
            jobs.error(lambda: ai_jobs.Lease(machine).acquire(), 'busy')
        finally:
            lease.close()
        runner = ai_jobs.Runner(machine, Path(root) / 'profile', 'p', background_jobs=owner)
        jobs.error(lambda: runner.start(jobs.package(), jobs.prepared(root), jobs.FakeAdapter(root)), 'shutting_down')
        assert not runner.active_path.exists()
        lease = ai_jobs.Lease(machine).acquire()
        lease.close()


def t_background_shutdown_keeps_lease_until_process_tree_cleanup_finishes():
    with tempfile.TemporaryDirectory() as root:
        machine = Path(root) / 'machine'
        owner = ai_jobs.BackgroundJobs()
        runner = ai_jobs.Runner(machine, Path(root) / 'profile', 'p', background_jobs=owner)
        adapter = jobs.FakeAdapter(root, 'tree')
        entered, release = threading.Event(), threading.Event()
        terminate = ai_jobs.terminate_tree
        def paused_cleanup(proc, tree=None):
            entered.set()
            assert release.wait(10), 'test did not release cleanup'
            terminate(proc, tree)
        closer = threading.Thread(target=owner.close)
        with patch.object(ai_jobs, 'terminate_tree', side_effect=paused_cleanup):
            try:
                runner.start(jobs.package(), jobs.prepared(root), adapter)
                jobs.wait_for(lambda: hasattr(adapter, 'scratch_output') and
                              (adapter.scratch_output / 'child.pid').exists())
                closer.start()
                assert entered.wait(5)
                jobs.error(lambda: ai_jobs.Lease(machine).acquire(), 'busy')
                assert closer.is_alive()
            finally:
                release.set()
                owner.close()
                if closer.ident is not None:
                    closer.join(15)
            lease = ai_jobs.Lease(machine).acquire()
            lease.close()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child_service(sys.argv[2], bool(int(sys.argv[3])))
    else:
        for name, fn in sorted(globals().copy().items()):
            if name.startswith('t_'):
                fn()
                print('PASS', name)

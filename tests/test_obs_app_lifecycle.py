#!/usr/bin/env python3
"""OBS exit confirmation and launch ordering. Run directly with Python."""
import contextlib
import io
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_racecast

m = test_racecast.m
ev = m._event_modules()[0]


class Clock:
    def __init__(self):
        self.now = 0
        self.polls = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def probe(self, timeout):
        self.polls.append((self.now, timeout))
        return self.now < 14


class Lifecycle(unittest.TestCase):
    def test_checked_platform_probes(self):
        for platform, present, absent in (
                ('win32', 'obs64.exe 123 Console', 'INFO: No tasks are running.'),
                ('darwin', '123\n', ''), ('linux', '123\n', '')):
            with self.subTest(platform=platform):
                for running, output in ((True, present), (False, absent)):
                    rc = 0 if platform == 'win32' or running else 1
                    def run(argv, rc=rc, output=output, **kw):
                        self.assertEqual(kw['timeout'], 0.75)
                        self.assertEqual(kw['errors'], 'replace')
                        return SimpleNamespace(returncode=rc, stdout=output)
                    self.assertEqual(ev.app_running_checked('obs', platform,
                                                            timeout=0.75, run=run), running)
                for error in (OSError('missing'), subprocess.TimeoutExpired('probe', 1)):
                    with self.assertRaises(OSError):
                        ev.app_running_checked('obs', platform,
                                               run=lambda *a, error=error, **k: (_ for _ in ()).throw(error))
                with self.assertRaises(OSError):
                    ev.app_running_checked('obs', platform,
                        run=lambda *a, **k: SimpleNamespace(returncode=2, stdout=''))
        with self.assertRaises(OSError):
            ev.app_running_checked('obs', 'win32',
                run=lambda *a, **k: SimpleNamespace(returncode=0, stdout=''))

    def test_waits_for_actual_exit(self):
        clock = Clock()
        progress = []
        self.assertTrue(ev.wait_for_app_exit('obs', clock=clock.clock, sleep=clock.sleep,
                         probe=clock.probe, progress=lambda: progress.append(clock.now)))
        self.assertEqual(clock.now, 14)
        self.assertEqual(progress, [0])
        self.assertTrue(all(0 < budget <= 30 - at for at, budget in clock.polls))

    def test_wait_is_bounded(self):
        clock = Clock()
        self.assertFalse(ev.wait_for_app_exit('obs', timeout=2, clock=clock.clock,
                         sleep=clock.sleep, probe=lambda timeout: True))
        self.assertEqual(clock.now, 2)

    def test_probe_cannot_confirm_exit_after_deadline(self):
        clock = Clock()
        def slow_absence(timeout):
            clock.now += timeout + 1
            return False
        self.assertFalse(ev.wait_for_app_exit('obs', timeout=2, clock=clock.clock,
                         sleep=clock.sleep, probe=slow_absence))

    def test_real_child_exit_is_observed_before_wait_returns(self):
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                                 stdin=subprocess.PIPE)
        live = []
        def release():
            live.append(child.poll() is None)
            child.stdin.write(b'x')
            child.stdin.flush()
        try:
            self.assertTrue(ev.wait_for_app_exit('obs', timeout=5, interval=0.01,
                probe=lambda timeout: child.poll() is None, progress=release))
            self.assertEqual(live, [True])
            self.assertIsNotNone(child.poll())
        finally:
            child.stdin.close()
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def test_spawn_waits_through_old_process_shutdown(self):
        clock = Clock()
        def wait(**kw):
            return ev.wait_for_app_exit('obs', clock=clock.clock, sleep=clock.sleep,
                                        probe=clock.probe)
        def spawn(*a, **kw):
            self.assertEqual(clock.now, 14, 'new instance overtook old shutdown')
            self.assertFalse(clock.probe(1))
            return SimpleNamespace(poll=lambda: None)
        with patch.object(m, '_wait_for_obs_exit', side_effect=wait):
            m._gui_spawn(['obs'], None, None, 'obs', popen=spawn, sleep=lambda _: None)

    def test_spawn_requires_exit_confirmation(self):
        calls = []
        def wait(**kw):
            calls.append('exit')
            return True
        def spawn(*a, **kw):
            calls.append('spawn')
            return SimpleNamespace(poll=lambda: None)
        with patch.object(m, '_wait_for_obs_exit', side_effect=wait):
            m._gui_spawn(['obs'], None, None, 'obs', popen=spawn, sleep=lambda _: None)
        self.assertEqual(calls, ['exit', 'spawn'])
        for result in (False, OSError('probe failed')):
            calls.clear()
            with patch.object(m, '_wait_for_obs_exit', side_effect=result if isinstance(result, Exception) else None,
                              return_value=result):
                with self.assertRaises(OSError):
                    m._gui_spawn(['obs'], None, None, 'obs', popen=spawn)
            self.assertEqual(calls, [])
        with patch.object(m, '_wait_for_obs_exit', side_effect=AssertionError('OBS-only')):
            m._gui_spawn(['discord'], None, None, 'discord', popen=spawn, sleep=lambda _: None)

    def test_manual_and_event_launch_refuse_overlap(self):
        with patch.object(m, '_wait_for_obs_exit', return_value=False), \
             patch.object(ev, 'launch_command', return_value=(['obs'], None)), \
             patch.object(m.subprocess, 'Popen', side_effect=AssertionError('second OBS')), \
             patch('install_apps.app_present', return_value=True), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, 'still running'):
                m.app_launch_cmd(['obs'])
            self.assertFalse(m._event_launch(ev, 'obs'))

    def test_quit_confirms_exit_and_preserves_budget(self):
        clock = Clock()
        def request(argv, **kw):
            self.assertNotIn('/F', argv)
            self.assertEqual(kw['timeout'], 30)
            clock.now += 4
            return SimpleNamespace(returncode=0)
        def wait(**kw):
            self.assertEqual(kw['timeout'], 26)
            return True
        out = io.StringIO()
        with patch.object(m.time, 'monotonic', side_effect=clock.clock), \
             patch.object(m.subprocess, 'run', side_effect=request), \
             patch.object(m, '_wait_for_obs_exit', side_effect=wait), \
             contextlib.redirect_stdout(out):
            m.app_quit_cmd(['obs'])
        self.assertIn('OBS exited', out.getvalue())
        self.assertNotIn('Asked obs', out.getvalue())

    def test_quit_does_not_claim_exit_on_timeout_or_probe_error(self):
        for result in (False, OSError('probe failed')):
            with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                 patch.object(m, '_wait_for_obs_exit', side_effect=result if isinstance(result, Exception) else None,
                              return_value=result), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    m.app_quit_cmd(['obs'])
        with patch.object(m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('quit', 30)):
            with self.assertRaisesRegex(SystemExit, 'timed out'):
                m.app_quit_cmd(['obs'])


if __name__ == '__main__':
    unittest.main()

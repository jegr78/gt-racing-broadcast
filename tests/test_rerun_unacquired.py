#!/usr/bin/env python3
"""CI reruns a run only when every failed job never got a hosted runner (#1080)."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('rerun_unacquired', os.path.join(ROOT, 'tools', 'rerun-unacquired.py'))
rr = importlib.util.module_from_spec(spec); spec.loader.exec_module(rr)

UNACQUIRED = 'The job was not acquired by Runner of type hosted even after multiple attempts'


def job(ident, conclusion, steps=0, runner=''):
    return dict(id=ident, conclusion=conclusion, steps=[{}]*steps, runner_name=runner)


def t_reruns_when_every_failed_job_never_got_a_runner():
    jobs = [job(1, 'success', 8, 'GitHub Actions 1'), job(2, 'cancelled'), job(3, 'failure')]
    notes = {2: [UNACQUIRED], 3: ['Due to capacity constraints, jobs targeting macOS arm64 runners ...', UNACQUIRED]}
    assert rr.should_rerun(1, jobs, notes) == [2, 3]


def t_never_reruns_a_real_failure():
    # One job that ran and failed makes the whole run a real failure.
    jobs = [job(2, 'cancelled'), job(4, 'failure', 6, 'GitHub Actions 7')]
    assert rr.should_rerun(1, jobs, {2: [UNACQUIRED], 4: [UNACQUIRED]}) == []
    # A job without steps but without GitHub's capacity message is not ours to retry.
    assert rr.should_rerun(1, [job(2, 'cancelled')], {2: ['The operation was canceled.']}) == []
    assert rr.should_rerun(1, [job(2, 'cancelled')], {}) == []
    # A run cancelled by a person or a newer push has no failed job with that message.
    assert rr.should_rerun(1, [job(1, 'success', 8, 'r')], {}) == []


def t_retries_are_bounded():
    jobs, notes = [job(2, 'cancelled')], {2: [UNACQUIRED]}
    assert rr.should_rerun(rr.MAX_ATTEMPTS-1, jobs, notes) == [2]
    assert rr.should_rerun(rr.MAX_ATTEMPTS, jobs, notes) == []


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn): fn(); print('PASS', name)

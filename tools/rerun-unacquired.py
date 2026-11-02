#!/usr/bin/env python3
"""Rerun a CI run whose failed jobs never got a GitHub-hosted runner (#1080).

GitHub cancels a job after about 15 minutes when no hosted runner takes it, with
the annotation "The job was not acquired by Runner of type hosted even after
multiple attempts" (seen for macOS arm64 under capacity constraints). Such a job
ran no step, so it says nothing about the code. A run is rerun only when EVERY
failed job is of that kind, and at most MAX_ATTEMPTS-1 times; a real failure is
never retried.

Usage (the rerun-unacquired workflow): rerun-unacquired.py --repo OWNER/REPO --run-id ID
"""
import argparse
import json
import subprocess
import sys

MAX_ATTEMPTS = 3
UNACQUIRED = 'was not acquired by Runner'
FAILED = ('failure', 'cancelled', 'timed_out')


def should_rerun(attempt, jobs, annotations):
    """The failed job IDs to rerun, or [] when the run must stay red."""
    if attempt >= MAX_ATTEMPTS:
        return []
    failed = [j for j in jobs if j.get('conclusion') in FAILED]
    unacquired = [j['id'] for j in failed
                  if not j.get('steps') and not j.get('runner_name')
                  and any(UNACQUIRED in note for note in annotations.get(j['id'], []))]
    return unacquired if failed and len(unacquired) == len(failed) else []


def _gh(*args):
    result = subprocess.run(['gh', *args], capture_output=True, text=True, errors='replace', check=True)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description='Rerun a CI run whose failed jobs never got a hosted runner.')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    run = json.loads(_gh('api', f'repos/{args.repo}/actions/runs/{args.run_id}'))
    jobs = json.loads(_gh('api', f'repos/{args.repo}/actions/runs/{args.run_id}/jobs?per_page=100'))['jobs']
    annotations = {}
    for job in jobs:
        if job.get('conclusion') in FAILED:
            notes = json.loads(_gh('api', f'repos/{args.repo}/check-runs/{job["id"]}/annotations'))
            annotations[job['id']] = [n.get('message', '') for n in notes]
    rerun = should_rerun(run['run_attempt'], jobs, annotations)
    if not rerun:
        print(f'run {args.run_id} attempt {run["run_attempt"]}: no rerun')
        return 0
    print(f'run {args.run_id} attempt {run["run_attempt"]}: {len(rerun)} job(s) never got a runner, rerunning')
    _gh('run', 'rerun', str(args.run_id), '--failed', '--repo', args.repo)
    return 0


if __name__ == '__main__':
    sys.exit(main())

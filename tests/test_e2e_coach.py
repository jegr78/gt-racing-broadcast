#!/usr/bin/env python3
"""Integrated private coaching acceptance against a real synthetic relay HTTP server."""
import sys
import contextlib
import io
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src/scripts'),str(ROOT/'tools'),str(Path(__file__).parent)]
import e2e_coach
from test_driver_radio_ui import RadioFixture,console


def t_replay_acceptance_exercises_real_controls_without_scoring_recorded_evidence():
    with RadioFixture(coaching=True) as f:
        f.practice.snap['replay']['active']=True
        f.practice.service.start()
        token=console.m.console_auth.mint_token(console.SECRET,'director')
        result=e2e_coach.check(f.url,'solo-pov',token,'director',timeout=5)
        assert result['status']=='pass' and result['history_entries']==1
        assert result['scored_laps']==0 and not result['active']
        assert f.f.access.read()['voice']['enabled'] is False
        assert f.f.access.read()['providers']['speech'] is False


def t_acceptance_refuses_nonowned_transports_before_any_request():
    with patch.object(e2e_coach.http,'http_request',side_effect=AssertionError('Acceptance attempted a nonowned request')):
        for base in ('http://198.51.100.20:1234','https://127.0.0.1:1234','http://127.0.0.1'):
            try:e2e_coach.check(base,'synthetic','token','driver')
            except ValueError:pass  # Only an explicitly owned loopback relay is a valid acceptance target.
            else:raise AssertionError('Acceptance allowed an unowned target')


def t_coach_only_rejects_real_league_and_interactive_modes_before_starting_services():
    import e2e as runner
    with patch.object(runner,'run_coach_only',side_effect=AssertionError('Incompatible mode reached runtime')):
        for flags in (['--real-league','not-a-real-profile'],['--keep'],['--visual'],['--playwright']):
            with contextlib.redirect_stderr(io.StringIO()):
                try:runner.main(['--coach-only']+flags)
                except SystemExit as error:assert error.code==2
                else:raise AssertionError('The dedicated Solo gate accepted incompatible runtime options')


if __name__=='__main__':
    for name,fn in globals().copy().items():
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

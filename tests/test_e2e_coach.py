#!/usr/bin/env python3
"""Integrated private coaching acceptance against a real synthetic relay HTTP server."""
import sys
from pathlib import Path
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


if __name__=='__main__':
    for name,fn in globals().copy().items():
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

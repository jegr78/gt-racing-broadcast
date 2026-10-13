#!/usr/bin/env python3
"""The Race Engineer desk's throttle/brake trace: a short, decimated input history
kept by the live store and served read-only through the gated `trace` route.
Run: python3 tests/test_engineer_live_inputs.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_policy
import gt7_telemetry as tm
import race_engineer_api as api
import race_engineer_live as live
from race_engineer_access import AccessError
from test_gt7_laps import _pkt


def _drive(store, clock, seconds, start=100.0):
    base = tm.parse_packet(_pkt(1, 30, 0))
    for i in range(int(seconds * 60)):              # GT7 sends 60 packets per second
        clock[0] = start + i / 60
        store.put(base._replace(throttle=255 if i % 2 else 0, brake=51), 1000 + i / 60)
    assert store.flush(10), 'worker did not drain accepted input'


def t_input_trace_is_decimated_windowed_and_scaled():
    clock = [0.0]
    with live.Store(lambda: True, clock=lambda: clock[0], queue_max=4096) as store:
        _drive(store, clock, 10)
        samples = store.inputs()['samples']
        span = samples[-1]['t'] - samples[0]['t']
        assert live.INPUT_TRACE_S - .2 <= span <= live.INPUT_TRACE_S, span
        assert 28 <= len(samples) / span <= 31, 'about 30 samples per second like the HUD'
        assert all(0 <= s['throttle'] <= 1 and s['brake'] == .2 for s in samples)
        assert set(samples[0]) == {'t', 'throttle', 'brake'}


def t_input_trace_since_returns_only_newer_samples():
    clock = [0.0]
    with live.Store(lambda: True, clock=lambda: clock[0], queue_max=4096) as store:
        _drive(store, clock, 2)
        samples = store.inputs()['samples']
        assert store.inputs(since=samples[-5]['t'])['samples'] == samples[-4:]
        assert store.inputs(since=samples[-1]['t'])['samples'] == []


def t_input_trace_clears_with_the_generation():
    clock, enabled = [0.0], [True]
    with live.Store(lambda: enabled[0], clock=lambda: clock[0], queue_max=4096) as store:
        _drive(store, clock, 1)
        assert store.inputs()['samples']
        enabled[0] = False
        store.refresh_enabled()
        assert store.inputs()['samples'] == [], 'a disabled or restarted source keeps no inputs'


def t_trace_route_is_engineer_gated_and_validates_since():
    allowed = console_policy.decide({console_policy.RACE_ENGINEER}, ['race-engineer', 'trace'])
    assert allowed == console_policy.ALLOW
    assert console_policy.decide(set(), ['race-engineer', 'trace']) != console_policy.ALLOW
    assert 'trace' in api.ROUTES
    clock = [0.0]
    with live.Store(lambda: True, clock=lambda: clock[0], queue_max=4096) as store:
        _drive(store, clock, 1)
        service = api.Service(store)
        try:
            full = service.request('trace', {}, None)
            assert full['samples'] and full['generation'] == store.generation and full['version'] == 1
            since = str(full['samples'][-3]['t'])
            assert len(service.request('trace', {'since': [since]}, None)['samples']) == 2
            for bad in ({'since': ['abc']}, {'since': ['1', '2']}, {'id': ['x']}):
                try:
                    service.request('trace', bad, None)
                except AccessError as exc:
                    assert exc.code == 'invalid_query'
                else:
                    raise AssertionError(f'{bad} must be rejected')
        finally:
            service.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""Live coach calibration preserves measured personal evidence and reference grants."""
import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import driver_coach_calibration as coach

OWNER = {'profile': 'solo', 'subject': 'driver-one'}


def lap(n, seconds=90, **changes):
    result = dict(id='lap-' + str(n), lap=n, session_id='live-session', car_id=1,
                  track_id='ring01', layout='Full', compound=None, stint_id=None,
                  state='confirmed', time_s=seconds, time_valid=True,
                  comparison_eligible=True, capture_complete=True, trace_complete=True,
                  data_quality='ok', analysis_role='regular', after_service=False,
                  strategy={}, context_settings={}, context_revision=0,
                  definition_fingerprint=None, settings_confirmed=False,
                  trace=[{'d': 0, 't': 0, 'speed_kmh': 100},
                         {'d': 5, 't': .18, 'speed_kmh': 100}])
    result.update(changes)
    return result


def started():
    c = coach.Calibration()
    c.start(OWNER)
    rejects(c.begin_focus, 'calibration_required')
    return c


def rejects(fn, code):
    try:
        fn()
    except coach.CoachError as error:
        assert error.code == code, (error.code, code)
    else:
        raise AssertionError('Expected coach rejection: ' + code)


def t_explicit_practice_start_and_two_lap_baseline_then_three_lap_readiness():
    c = coach.Calibration()
    assert c.ingest(lap(1))['phase'] == 'inactive'
    for mode in ('race', 'qualifying', '', None):
        rejects(lambda mode=mode: c.start(OWNER, mode=mode), 'invalid_mode')
    rejects(lambda: c.start(OWNER, objective='optimal'), 'invalid_objective')
    rejects(lambda: c.start({'profile': 'solo'}), 'invalid_owner')
    c.start(OWNER)
    assert c.snapshot()['objective'] == 'balanced'
    assert c.ingest(lap(2))['phase'] == 'calibrating'
    before = c.ingest(lap(3, 89))
    assert before['phase'] == 'provisional' and before['baseline']['lap_ids'] == ['lap-2', 'lap-3']
    assert before['baseline']['time_s'] == 89.5
    assert c.ingest(lap(4, 88))['phase'] == 'ready'
    assert c.snapshot()['baseline'] == before['baseline'], 'third lap rewrote the starting baseline'
    assert c.snapshot()['calibration_ids'] == ['lap-2', 'lap-3', 'lap-4']
    assert {'compound', 'definition_fingerprint', 'strategy'} <= set(c.snapshot()['unknown'])
    assert 'trace' not in str(c.snapshot())
    stopped = c.stop()
    assert stopped['phase'] == 'stopped' and c.reference() is None
    assert c.ingest(lap(5))['phase'] == 'stopped'


def t_unsuitable_duplicate_and_stale_laps_do_not_advance_or_replace_evidence():
    c = started()
    c.ingest(lap(2)); c.ingest(lap(3))
    baseline = c.snapshot()['baseline']
    for changes in ({'state': 'provisional'}, {'comparison_eligible': False},
                    {'capture_complete': False}, {'trace_complete': False},
                    {'time_valid': False}, {'data_quality': 'interrupted'},
                    {'analysis_role': 'warmup'}, {'analysis_role': 'pit'},
                    {'analysis_role': 'partial'}, {'analysis_role': 'first'},
                    {'analysis_role': None}, {'analysis_role': 'unknown'},
                    {'after_service': True}, {'time_s': float('nan')},
                    {'time_s': float('inf')}, {'time_s': 10 ** 400}, {'time_s': True}, {'time_s': 0}):
        result = c.ingest(lap(4, **changes))
        assert not result['accepted'], changes
        assert result['baseline'] == baseline and result['phase'] == 'provisional'
    assert not c.ingest(lap(3))['accepted']
    assert not c.ingest(lap(1))['accepted']
    assert c.snapshot()['calibration_ids'] == ['lap-2', 'lap-3']


def t_focus_freezes_one_actual_reference_and_promotes_only_after_completion():
    c = started(); c.ingest(lap(2, 90)); c.ingest(lap(3, 89)); c.begin_focus()
    reference = c.reference()
    assert reference['id'] == 'lap-3'
    c.ingest(lap(4, 87)); c.ingest(lap(5, 88))
    assert c.reference() == reference, 'working reference drifted during the focus'
    baseline = c.snapshot()['baseline']
    c.end_focus()
    assert c.reference()['id'] == 'lap-4'
    assert c.snapshot()['baseline'] == baseline
    copy_of_laps = c.baseline_laps(); copy_of_laps[0]['trace'].clear()
    assert c.baseline_laps()[0]['trace'], 'caller changed frozen baseline data'


def t_relevant_changes_and_measurement_corrections_invalidate_with_provenance():
    for changes in ({'session_id': 'new'}, {'car_id': 2}, {'track_id': 'other'},
                    {'layout': 'Reverse'}, {'compound': 'RM'}, {'stint_id': 'fresh'},
                    {'strategy': {'fuel_map': 6}}, {'definition_fingerprint': 'v2'},
                    {'context_settings': {'bop': True}, 'settings_confirmed': True}):
        c = started(); c.ingest(lap(2)); c.ingest(lap(3)); c.begin_focus()
        before = c.snapshot()
        after = c.ingest(lap(4, **changes))
        assert after['phase'] == 'calibrating' and not after['focus_active'], changes
        assert after['epoch'] != before['epoch']
        assert after['invalidations'][-1]['baseline_id'] == before['baseline']['id']
    c = started(); c.ingest(lap(2)); c.ingest(lap(3))
    old = c.snapshot(); c.ingest(lap(2, 91))
    assert c.snapshot()['epoch'] != old['epoch']
    assert c.snapshot()['phase'] == 'calibrating'
    c.ingest(lap(3)); c.set_objective('consistency')
    assert c.snapshot()['phase'] == 'calibrating' and c.snapshot()['baseline'] is None
    assert c.snapshot()['objective'] == 'consistency'


def t_external_references_require_current_access_and_compatible_known_identity():
    c = started(); c.ingest(lap(2)); c.ingest(lap(3))
    reference = lap(7, 85, session_id='earlier')
    permitted = [True]
    c.select_reference(reference, lambda: permitted[0])
    assert c.reference()['id'] == 'lap-7'
    c.begin_focus(); c.ingest(lap(4, 80)); c.end_focus()
    assert c.reference()['id'] == 'lap-7', 'personal PB replaced explicitly selected reference'
    before = c.snapshot()['epoch']; permitted[0] = False
    assert c.reference()['id'] != 'lap-7'
    assert c.snapshot()['epoch'] != before and c.snapshot()['reference']['source'] == 'personal'
    rejects(lambda: c.select_reference(reference, lambda: False), 'reference_forbidden')
    for changes in ({'car_id': 2}, {'track_id': None}, {'layout': 'Reverse'},
                    {'compound': 'RM'}, {'strategy': {'fuel_map': 2}},
                    {'definition_fingerprint': 'different'}, {'sector_variant_id': 'other'}):
        bad = copy.deepcopy(reference); bad.update(changes)
        # A declared strategy versus no known strategy remains unknown, not conflicting.
        if 'strategy' in changes:
            c = started(); c.ingest(lap(2, strategy={'fuel_map': 1})); c.ingest(lap(3, strategy={'fuel_map': 1}))
        rejects(lambda c=c, bad=bad: c.select_reference(bad, lambda: True), 'reference_incompatible')
    c = started(); c.ingest(lap(2, track_id=None)); c.ingest(lap(3, track_id=None))
    assert c.snapshot()['phase'] == 'provisional', 'unknown track blocked personal same-session evidence'
    rejects(lambda: c.select_reference(reference, lambda: True), 'reference_incompatible')
    unknown = copy.deepcopy(reference); unknown['track_id'] = None
    rejects(lambda: c.select_reference(unknown, lambda: True), 'reference_incompatible')


def t_same_session_stint_and_known_settings_conflicts_cannot_be_selected():
    c = started(); c.ingest(lap(2, stint_id='old')); c.ingest(lap(3, stint_id='old'))
    rejects(lambda: c.select_reference(lap(7, stint_id='fresh'), lambda: True), 'reference_incompatible')
    c = started(); c.ingest(lap(2, compound='RM')); c.ingest(lap(3, compound='RM'))
    rejects(lambda: c.select_reference(lap(7, session_id='older', compound=None), lambda: True), 'reference_incompatible')
    c = started()
    for n in (2, 3):
        c.ingest(lap(n, context_settings={'bop': False}, settings_confirmed=True))
    rejects(lambda: c.select_reference(lap(7, session_id='old', context_settings={'bop': True},
                                         settings_confirmed=True), lambda: True), 'reference_incompatible')


def t_input_bounds_and_deduplication_are_bounded_without_old_lap_recounting():
    c = started()
    bad = lap(2); bad['trace'] = [{'d': 0, 'speed_kmh': float('nan')}]
    assert not c.ingest(bad)['accepted']
    oversized = lap(2); oversized['trace'] = [{'d': 0, 'padding': 'x' * (coach.LAP_BYTES_MAX + 1)}]
    assert not c.ingest(oversized)['accepted']
    c.ingest(lap(2)); c.ingest(lap(3))
    original = c.snapshot()['baseline']
    for n in range(4, coach.SEEN_MAX + 30):
        c.ingest(lap(n))
    assert c.snapshot()['retained_laps'] <= 5
    assert c.snapshot()['dedup_entries'] <= coach.SEEN_MAX
    assert not c.ingest(lap(4))['accepted'], 'evicted old lap counted again'
    assert c.snapshot()['baseline'] == original


def t_corrected_eligibility_invalidates_old_baseline_without_counting_bad_evidence():
    for changes in ({'comparison_eligible': False}, {'analysis_role': 'warmup'},
                    {'time_valid': False}, {'state': 'unconfirmed'}):
        c = started(); c.ingest(lap(2)); c.ingest(lap(3)); c.begin_focus()
        before = c.snapshot()
        result = c.ingest(lap(2, **changes))
        assert not result['accepted']
        assert result['baseline'] is None and result['epoch'] != before['epoch'], changes
        assert not result['focus_active']


def t_reference_read_fails_closed_when_authorization_becomes_unreadable():
    c = started(); c.ingest(lap(2)); c.ingest(lap(3))
    broken = [False]
    def authorize():
        if broken[0]:
            raise RuntimeError('synthetic unavailable permissions')
        return True
    c.select_reference(lap(7, 80, session_id='older'), authorize)
    broken[0] = True
    assert c.reference()['id'] != 'lap-7'


def t_stale_context_cannot_reset_the_current_cohort_and_free_notes_do_not_drift_it():
    c = started(); c.ingest(lap(5)); c.ingest(lap(6)); before = c.snapshot()
    result = c.ingest(lap(4, car_id=2, compound='RS'))
    assert not result['accepted'] and result['epoch'] == before['epoch']
    assert result['baseline'] == before['baseline']
    result = c.ingest(lap(7, strategy={'note': 'Free practice note'}))
    assert result['phase'] == 'ready' and result['baseline'] == before['baseline']
    malformed = lap(8, session_id='earlier', strategy={'targets': 'not a target map'})
    rejects(lambda: c.select_reference(malformed, lambda: True), 'reference_incompatible')


def t_retired_session_packets_cannot_roll_calibration_back_and_tombstones_are_bounded():
    c = started(); c.ingest(lap(5)); c.ingest(lap(6))
    c.ingest(lap(1, session_id='new-session'))
    before = c.snapshot()
    result = c.ingest(lap(7))
    assert not result['accepted'] and result['epoch'] == before['epoch']
    assert result['calibration_ids'] == before['calibration_ids']
    for n in range(coach.SESSION_MAX + 1):
        result = c.ingest(lap(1, session_id='session-' + str(n)))
    assert not result['active'] and result['reason'] == 'session_limit'
    assert c.ingest(lap(7))['active'] is False
    c.start(OWNER)
    assert c.ingest(lap(7))['accepted'], 'explicit restart did not clear session tombstones'


def t_finite_positive_baseline_inputs_cannot_overflow_or_underflow():
    import json
    for number in (1e308, 5e-324):
        c = started(); c.ingest(lap(2, number)); result = c.ingest(lap(3, number))
        assert result['baseline']['time_s'] == number
        json.dumps(result, allow_nan=False)


def t_session_identity_survives_objective_changes_and_unavailable_source_resets():
    for reset in (lambda c: c.set_objective('pace'), lambda c: c.invalidate('source_unavailable')):
        c = started(); c.ingest(lap(5)); c.ingest(lap(6)); reset(c)
        c.ingest(lap(1, session_id='new-session'))
        before = c.snapshot()
        result = c.ingest(lap(7))
        assert not result['accepted'] and result['epoch'] == before['epoch']
        assert result['retired_sessions'] == 1


def t_real_live_store_packets_calibrate_without_external_target():
    from test_gt7_laps import FakeCars, FakeTracks, write_circle_recording
    from test_race_engineer_live import feed
    from race_engineer_live import Store
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        with Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), queue_max=4096) as store:
            feed(store, path)
            c = started()
            for row in store.snapshot()['laps']:
                detail = store.detail(row['id'])
                if detail['state'] == 'ready':
                    c.ingest(detail['lap'])
            result = c.snapshot()
            assert result['phase'] == 'ready'
            assert len(result['calibration_ids']) == 3
            assert [row['lap'] for row in c.baseline_laps()] == [2, 3]
            assert c.reference()['lap'] == 3


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn(); print('PASS', name)

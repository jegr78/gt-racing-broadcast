#!/usr/bin/env python3
"""Persistent, optional recording/session context and its comparison policy."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'scripts'))
import gt7_context as c
import gt7_recording as rec


def recording(d):
    w = rec.RecordingWriter(d, 'Test', 'dev', queue_max=0, on_open=c.attach_prepared)
    w.put(1000.0, 'A', bytes(296))
    w.close()
    return w.path


def data():
    return {'notes': [{'id': 'note1', 'scope': 'lap', 'session': 1, 'lap': 8,
                       'text': 'Traffic', 'position': {'x': 1.0, 'z': 2.0, 'd': 300.0}}],
            'sessions': {'1': {'settings': {'laps': 20, 'bop': True, 'fixed_setup': True,
                                           'pit_stops': 1, 'pit_rule': 'minimum',
                                           'required_tyres': ['RM', 'RH'], 'fuel_x': 4,
                                           'tyre_x': 5, 'refuel_lps': 5},
                               'confirmed': True, 'stints': [
                  {'id': 'medium', 'start_lap': 1, 'compound': 'RM', 'confirmed': True,
                   'tyre_service': False, 'warmup_laps': 0, 'strategy': {'shortshift': True}},
                  {'id': 'hard', 'start_lap': 13, 'start_recording_s': 1290,
                   'compound': 'RH', 'confirmed': True, 'tyre_service': True,
                   'warmup_laps': 1, 'strategy': {'shortshift': False}}]}}}


def t_save_reload_restore_and_source_capture_unchanged():
    with tempfile.TemporaryDirectory() as d:
        p = recording(d)
        original = Path(p).read_bytes()
        s = c.Store.for_recording(p)
        empty = s.read()
        assert empty['revision'] == 0 and empty['data']['sessions'] == {}
        first = s.save(data(), expected=0)
        assert first['revision'] == 1
        assert c.Store.for_recording(p).read()['data'] == first['data']
        changed = json.loads(json.dumps(first['data']))
        changed['notes'][0]['text'] = 'More traffic'
        second = s.save(changed, expected=1)
        assert second['revision'] == 2 and s.history()[0]['revision'] == 1
        restored = s.restore(1, expected=2)
        assert restored['revision'] == 3 and restored['data']['notes'][0]['text'] == 'Traffic'
        assert Path(p).read_bytes() == original, 'context must not mutate the raw capture'
        exported = s.export()
        assert 'history' not in exported and 'draft' not in exported
        assert exported['data'] == first['data']


def t_conflict_and_invalid_data_preserve_last_good_context():
    with tempfile.TemporaryDirectory() as d:
        s = c.Store.for_recording(recording(d))
        s.save(data(), expected=0)
        try:
            s.save(data(), expected=0)
        except c.Conflict:
            pass  # expected stale-revision refusal
        else:
            raise AssertionError('stale revisions must not overwrite another edit')
        for bad in ({'notes': 'not a list'}, {'sessions': {'../x': {}}},
                    {'sessions': {'1': {'settings': {'fuel_x': float('nan')}}}},
                    {'sessions': {'1': {'settings': {'bop': 'true'}}}}):
            try:
                s.save(bad, expected=1)
            except ValueError:
                pass  # invalid input must preserve the prior revision
            else:
                raise AssertionError('invalid fields must not become confirmed context')
        assert s.read()['revision'] == 1
        saved = s.save(s.read()['data'], expected=1, draft={'fuel_x': '1e'})
        assert saved['revision'] == 1 and saved['draft'] == {'fuel_x': '1e'}


def t_prepared_context_is_consumed_once_and_templates_are_snapshots():
    with tempfile.TemporaryDirectory() as d:
        recdir = os.path.join(d, 'telemetry-recordings')
        prep = c.Store.prepared(recdir)
        prep.save(data(), expected=0)
        p = recording(recdir)
        assert c.Store.for_recording(p).read()['data']['sessions']['1']['settings']['laps'] == 20
        assert c.Store.prepared(recdir).read()['data']['sessions'] == {}, 'attach preparation once'
        next_path = recording(recdir)
        assert c.Store.for_recording(next_path).read()['revision'] == 0
        c.save_template(recdir, 'race', 'Race test', data()['sessions']['1']['settings'])
        copied = c.apply_template(c.Store.for_recording(next_path).read()['data'],
                                  c.templates(recdir)['templates']['race'], session=1)
        c.Store.for_recording(next_path).save(copied, expected=0)
        c.save_template(recdir, 'race', 'Race test', {'laps': 10})
        assert c.Store.for_recording(next_path).read()['data']['sessions']['1']['settings']['laps'] == 20
        inherited = c.copy_session(data(), 1, 2)
        assert inherited['sessions']['2']['confirmed'] is False
        assert inherited['sessions']['2']['settings']['laps'] == 20


def t_mixed_pit_lap_and_configurable_warmup_keep_manual_and_measured_evidence():
    base = {'session': 1, 'pace_eligible': True, 'capture_complete': True,
            'lap_role': 'regular', 'start_t_s': 1100, 'end_t_s': 1300}
    pit = c.annotate(data(), dict(base, lap=13, lap_role='pit', pace_eligible=False))
    assert pit['compounds'] == ['RM', 'RH'] and not pit['comparison_eligible']
    warm = c.annotate(data(), dict(base, lap=14, start_t_s=1300, end_t_s=1410))
    assert warm['compound'] == 'RH' and warm['analysis_role'] == 'warmup'
    assert not warm['comparison_eligible']
    regular = c.annotate(data(), dict(base, lap=15, start_t_s=1410, end_t_s=1520))
    assert regular['comparison_eligible'] and regular['strategy']['shortshift'] is False
    changed = data()
    changed['sessions']['1']['stints'][1]['warmup_laps'] = 0
    assert c.annotate(changed, dict(base, lap=14))['comparison_eligible']
    unknown = c.annotate({}, dict(base, lap=5))
    assert unknown['comparison_eligible'] and not unknown['context_confirmed']
    assert unknown['context_warnings'], 'unknown context must not masquerade as confirmed conditions'


def t_confirmed_service_splits_the_lap_at_its_actual_recording_time():
    row = {'session': 1, 'lap': 13, 'pace_eligible': True, 'capture_complete': True,
           'lap_role': 'regular', 'start_t_s': 1100, 'end_t_s': 1300}
    result = c.annotate(data(), row)
    assert [(m['compound'], m['start_t_s'], m['end_t_s']) for m in result['memberships']] == [
        ('RM', 1100, 1290), ('RH', 1290, 1300)]
    assert result['analysis_role'] == 'pit' and not result['comparison_eligible']
    assert row['lap_role'] == 'regular' and row['pace_eligible'], 'manual context does not rewrite measurements'
    assert result['context_warnings'], 'a confirmed service without matching inference remains visible'
    changed = data()
    changed['sessions']['1']['stints'][1]['start_recording_s'] = 2000
    bad = c.annotate(changed, row)
    assert not bad['context_confirmed'] and 'Service time outside recorded lap' in bad['context_warnings']


def t_ui_and_recorder_processes_cannot_both_overwrite_the_same_revision():
    import subprocess
    import time
    with tempfile.TemporaryDirectory() as d:
        path = recording(d)
        store = c.Store.for_recording(path)
        store.save(data(), expected=0)
        gate = os.path.join(d, 'go')
        script = """
import sys,time,os
sys.path.insert(0,sys.argv[1]);import gt7_context as c
store=c.Store.for_recording(sys.argv[2]);value=store.read()['data']
value['notes'][0]['text']=sys.argv[3]
real=c._atomic
def delayed(path,doc,compressed=False):
 if path==store.path and sys.argv[3]=='A':
  print('ready',flush=True)
  deadline=time.monotonic()+10
  while not os.path.exists(sys.argv[4]):
   if time.monotonic()>deadline:raise RuntimeError('gate timeout')
   time.sleep(.01)
 return real(path,doc,compressed)
c._atomic=delayed
print('loaded',flush=True)
try:store.save(value,expected=1);print('saved',flush=True)
except c.Conflict:print('conflict',flush=True)
"""
        argv = [sys.executable, '-c', script, str(Path(c.__file__).parent), path]
        a = subprocess.Popen(argv + ['A', gate], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, errors='replace')
        b = None
        try:
            assert a.stdout.readline().strip() == 'loaded'
            assert a.stdout.readline().strip() == 'ready'
            b = subprocess.Popen(argv + ['B', gate], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, errors='replace')
            assert b.stdout.readline().strip() == 'loaded'
            time.sleep(.08)
            Path(gate).touch()
            ao, ae = a.communicate(timeout=15)
            bo, be = b.communicate(timeout=15)
            assert a.returncode == b.returncode == 0, (ae, be)
            assert ao.strip() == 'saved' and bo.strip() == 'conflict', \
                'two processes editing revision 1 must not both save revision 2'
            assert store.read()['revision'] == 2 and store.read()['data']['notes'][0]['text'] == 'A'
        finally:
            Path(gate).touch()
            for proc in (a, b):
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)


def t_deleted_capture_cannot_be_recreated_by_a_queued_context_edit():
    with tempfile.TemporaryDirectory() as d:
        path = recording(d)
        store = c.Store.for_recording(path)
        os.remove(path)
        try:
            store.save(data(), expected=0)
        except ValueError as exc:
            assert str(exc) == 'recording no longer available'
        else:
            raise AssertionError('a queued edit must not write orphan context after deletion')
        assert not Path(store.path).exists()


def t_delete_owns_context_history_but_preserves_templates():
    with tempfile.TemporaryDirectory() as d:
        p = recording(d)
        s = c.Store.for_recording(p)
        s.save(data(), expected=0)
        s.save({}, expected=1)
        c.save_template(d, 'race', 'Race', {'laps': 20})
        c.delete_recording(p)
        assert not Path(s.path).exists() and not Path(s.history_dir).exists()
        assert c.templates(d)['templates']['race']['settings']['laps'] == 20


def t_midlap_strategy_changes_preserve_both_segments():
    context = data()
    context['sessions']['1']['stints'][0]['strategy_changes'] = [
        {'lap': 8, 'recording_s': 750, 'strategy': {'shortshift': False, 'fuel_map': 1}}]
    lap = {'session': 1, 'lap': 8, 'start_t_s': 700, 'end_t_s': 800,
           'lap_role': 'regular', 'pace_eligible': True}
    row = c.annotate(context, lap)
    assert len(row['memberships']) == 2, 'timestamped strategy change must split the lap'
    assert row['memberships'][0]['end_t_s'] == 750
    assert row['memberships'][1]['start_t_s'] == 750
    assert row['strategies'] == [{'shortshift': True}, {'shortshift': False, 'fuel_map': 1}]
    assert row['strategy'] == {}, 'mixed strategy must not imply one known strategy'
    assert row['lap_role'] == 'regular' and row['analysis_role'] == 'regular'


def t_unconfirmed_proposals_cannot_exclude_measured_comparisons():
    proposed = data()
    proposed['sessions']['1']['confirmed'] = False
    proposed['sessions']['1']['settings']['fuel_x'] = 99
    proposed['sessions']['1']['stints'][0]['confirmed'] = False
    lap = {'session': 1, 'lap': 8, 'start_t_s': 700, 'end_t_s': 800,
           'lap_role': 'regular', 'pace_eligible': True}
    unknown = c.annotate(proposed, lap)
    reference = c.annotate({}, lap)
    reference['context_settings'] = {'fuel_x': 4}
    reference['settings_confirmed'] = True
    assert c.compatible(reference, unknown), 'proposed conditions must not govern comparisons'
    assert unknown['strategy'] == {}, 'unconfirmed stint strategy remains a proposal'


def t_incomplete_or_unscoped_location_cannot_replace_valid_notes():
    with tempfile.TemporaryDirectory() as td:
        store = c.Store.for_recording(recording(td))
        store.save(data(), 0)
        for position, scope in (({'d': 100}, 'lap'), ({'x': 1, 'z': 2}, 'session')):
            changed = data()
            changed['notes'][0].update(position=position, scope=scope)
            try:
                store.save(changed, 1)
            except ValueError:
                pass  # an incomplete position is an invalid edit, not an invented location
            else:
                raise AssertionError('location notes require world X/Z and a source lap')
            assert store.read()['revision'] == 1 and store.read()['data']['notes'] == data()['notes']


def t_mixed_known_strategy_cannot_enter_a_default_comparison_as_unknown():
    context = data()
    context['sessions']['1']['stints'][0]['strategy'] = {'fuel_map': 1}
    context['sessions']['1']['stints'][0]['strategy_changes'] = [
        {'lap': 8, 'recording_s': 750, 'strategy': {'fuel_map': 2}}]
    base = {'session': 1, 'start_t_s': 700, 'end_t_s': 800,
            'lap_role': 'regular', 'pace_eligible': True}
    reference = c.annotate(context, dict(base, lap=7))
    mixed = c.annotate(context, dict(base, lap=8))
    assert not c.compatible(reference, mixed), 'mixed known strategies must not behave as unknown'
    assert not mixed['comparison_eligible'] and not mixed['context_confirmed']
    assert mixed['pace_eligible'], 'context never rewrites measured pace eligibility'
    assert 'Mixed fuel strategy within lap' in mixed['context_warnings']
    unknown = c.annotate({}, dict(base, lap=9))
    assert c.compatible(unknown, reference), 'missing strategy still allows labelled inspection'


def t_economy_target_comparison_checks_each_known_gear_without_inventing_missing_targets():
    reference = {'strategy': {'targets': {'2': 6000}}}
    candidate = {'comparison_eligible': True, 'strategy': {'targets': {'2': 6100}}}
    assert not c.compatible(reference, candidate), 'different known RPM targets must be excluded'
    candidate['strategy']['targets'] = {'3': 6200}
    assert c.compatible(reference, candidate), 'disjoint partial targets are unknown, not contradictory'
    candidate['strategy']['targets'] = {'2': 6000.0}
    assert c.compatible(reference, candidate), 'equivalent integer/float targets match'


def t_integral_revision_numbers_resolve_the_same_saved_history():
    with tempfile.TemporaryDirectory() as td:
        store = c.Store.for_recording(recording(td))
        store.save(data(), 0)
        store.save({}, 1)
        restored = store.restore(1.0, 2.0)
        assert restored['revision'] == 3 and restored['data']['notes'] == data()['notes']


def invalid(call, message):
    try:
        call()
    except ValueError:
        return
    raise AssertionError(message)


def t_json_shape_number_identifier_and_size_guards():
    invalid(lambda: c._object(None), 'non-object context must be rejected')
    invalid(lambda: c._list([None] * (c.MAX_ITEMS + 1)), 'oversized item lists must be rejected')
    invalid(lambda: c._token('../other'), 'unsafe identifiers must be rejected')
    invalid(lambda: c._text('x' * 16001), 'oversized text must be rejected')
    invalid(lambda: c._keys({'typo': 1}, {'notes'}), 'unsupported fields must be rejected')
    invalid(lambda: c._number(True, 'number'), 'booleans must not become numeric settings')
    invalid(lambda: c._number(1.5, 'lap', True), 'fractional laps must be rejected')
    invalid(lambda: c._bounded({'text': 'x' * c.MAX_BYTES}), 'oversized JSON must be rejected')
    changed = data()
    changed['notes'][0]['position']['x'] = True
    invalid(lambda: c.validate_data(changed), 'boolean coordinates must be rejected')


def t_loaded_context_size_format_and_identity_are_enforced():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td, 'large.json')
        path.write_text(json.dumps({'text': 'x' * c.MAX_BYTES}))
        try:
            c._load(str(path))
        except ValueError as exc:
            assert str(exc) == 'context is too large', 'oversized reads must report the bounded size error'
        else:
            raise AssertionError('oversized context files must be rejected')
        store = c.Store.for_recording(recording(td))
        doc = store.save(data(), 0)
        bad = dict(doc, version=999)
        c._atomic(store.path, bad)
        invalid(store.read, 'unsupported context versions must be rejected')
        bad = dict(doc, source_id='another-recording')
        c._atomic(store.path, bad)
        invalid(store.read, 'foreign context identities must be rejected')


def t_replaced_capture_identity_cannot_receive_old_context():
    with tempfile.TemporaryDirectory() as td:
        path = recording(td)
        store = c.Store.for_recording(path)
        store.save(data(), 0)
        head, payload = Path(path).read_bytes().split(b'\n', 1)
        header = json.loads(head)
        header['recording_id'] = 'f' * 32
        Path(path).write_bytes(json.dumps(header).encode() + b'\n' + payload)
        invalid(lambda: store.save({}, 1), 'a replaced capture must reject old context edits')
        assert store.read()['revision'] == 1


def t_context_metadata_is_valid_json_and_unsupported_history_is_not_restored():
    with tempfile.TemporaryDirectory() as td:
        store = c.Store.for_recording(recording(td))
        doc = store.save(data(), 0)
        corrupt = dict(doc, updated_at=float('nan'))
        Path(store.path).write_text(json.dumps(corrupt))
        invalid(store.read, 'non-finite context metadata must be rejected')
        c._atomic(store.path, doc)
        previous = dict(doc, version=999)
        c._atomic(os.path.join(store.history_dir, '1.json.gz'), previous, compressed=True)
        invalid(lambda: store.restore(1, 1), 'unsupported history versions must not be restored')
        assert store.read()['revision'] == 1


def t_explicit_lap_role_wins_over_warmup_but_unknown_restores_derivation():
    context = data()
    context['sessions']['1']['stints'][1]['warmup_laps'] = 2
    context['sessions']['1']['lap_roles'] = {'14': 'regular', '15': 'unknown'}
    base = {'session': 1, 'pace_eligible': True, 'lap_role': 'regular',
            'start_t_s': 1300, 'end_t_s': 1410}
    ready = c.annotate(context, dict(base, lap=14))
    assert ready['analysis_role'] == 'regular' and ready['comparison_eligible'], \
        'explicit manual regular role must override automatic warmup'
    warm = c.annotate(context, dict(base, lap=15))
    assert warm['analysis_role'] == 'warmup' and not warm['comparison_eligible']
    context['sessions']['1']['lap_roles']['13'] = 'unknown'
    pit = c.annotate(context, dict(base, lap=13, start_t_s=1100, end_t_s=1300))
    assert pit['analysis_role'] == 'pit', 'unknown role must permit confirmed service derivation'


def t_new_inferred_service_is_not_confirmed_by_an_older_stint_declaration():
    base = {'session': 1, 'pace_eligible': True, 'lap_role': 'regular',
            'start_t_s': 1700, 'end_t_s': 1810, 'lap': 18, 'after_service': True}
    row = c.annotate(data(), base)
    assert not row['context_confirmed'] and row['context_warnings'], \
        'an older confirmed service cannot confirm a new inferred event'
    context = data()
    context['sessions']['1']['stints'][1].update(compound='RM', tyre_service=False)
    row = c.annotate(context, dict(base, lap=14, start_t_s=1300, end_t_s=1410))
    assert row['context_confirmed'] and not row['context_warnings'], \
        'explicit no-tyre-change at the matching service is known, not unknown'


def t_a_timed_first_declared_stint_retains_unknown_pre_service_membership():
    context = data()
    context['sessions']['1']['stints'] = [context['sessions']['1']['stints'][1]]
    pit = {'session': 1, 'lap': 13, 'pace_eligible': False, 'lap_role': 'pit',
           'capture_complete': True, 'start_t_s': 1100, 'end_t_s': 1300}
    lookup = {(1, 13): pit}
    row = c.annotate(context, pit)
    assert [(m['compound'], m['start_t_s'], m['end_t_s']) for m in row['memberships']] == [
        (None, 1100, 1290), ('RH', 1290, 1300)], 'first timed service must not invent the earlier compound'
    warm = c.annotate(context, dict(pit, lap=14, start_t_s=1300, end_t_s=1410,
                                    lap_role='regular', pace_eligible=True), lap_lookup=lookup)
    assert warm['analysis_role'] == 'warmup', 'first full lap follows the timed initial service'


def t_conflicting_service_times_and_compound_statements_stay_unconfirmed():
    context = data()
    pit = {'session': 1, 'lap': 13, 'capture_complete': True,
           'start_t_s': 1100, 'end_t_s': 1300}
    lap = dict(pit, lap=15, start_t_s=1410, end_t_s=1520, pace_eligible=True, lap_role='regular')
    lookup = {(1, 13): pit}
    context['sessions']['1']['stints'][1]['start_recording_s'] = 2000
    row = c.annotate(context, lap, lap_lookup=lookup)
    assert not row['context_confirmed'] and not row['comparison_eligible'], \
        'a conflicting service time stays visible throughout its stint'
    assert 'Service time outside recorded lap' in row['context_warnings']
    context = data()
    context['sessions']['1']['stints'][1]['tyre_service'] = False
    row = c.annotate(context, lap, lap_lookup=lookup)
    assert not row['context_confirmed'] and not row['comparison_eligible'], \
        'a compound change cannot silently agree with no tyre change'
    assert row['pace_eligible'] and row['compound'] == 'RH'


def t_restoring_identical_values_still_creates_a_traceable_revision():
    with tempfile.TemporaryDirectory() as td:
        store = c.Store.for_recording(recording(td))
        store.save(data(), 0)
        store.save({}, 1)
        store.restore(1, 2)
        restored = store.restore(1, 3)
        assert restored['revision'] == 4, 'every restore must create a new revision'
        assert store.history()[0]['revision'] == 3


if __name__ == '__main__':
    for name, fn in sorted(globals().copy().items()):
        if name.startswith('t_') and callable(fn):
            fn()
            print('ok', name)
    print('ALL PASS')

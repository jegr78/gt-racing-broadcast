#!/usr/bin/env python3
"""Deterministic radio replay cases, numerical evidence and uncertainty."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import driver_radio_assessment as radio


def lap(n, seconds=90, fuel=3, **values):
    return dict(id='L'+str(n), session_id='s', lap=n, state='confirmed',
                comparison_eligible=True, time_s=seconds, fuel_burn_l=fuel,
                fuel_used_l=fuel, car_id=1, track_id='track', compound='RM',
                analysis_role='regular', length_m=4500, capture_complete=True,
                context_settings={}, strategy={}, tyre_age_laps=n, **values)


def snapshot(times=(90, 90, 90, 91, 91, 91)):
    return dict(generation='g', session_id='s', context_revision=0, revision=10,
                receipt={'state':'live'}, replay={'active':False},
                current=dict(id='current', session_id='s', lap=len(times)+1,
                             car_id=1, track_id='track', compound='RM', observed_from_line=True,
                             distance_m=2250, length_m=2250, elapsed_s=45, tyre_age_laps=len(times)+1),
                instruments=dict(fuel_l=15, tyres_c=[80,81,82,83]),
                laps=[lap(i+1,t) for i,t in enumerate(times)], fuel_avg_l=7)


def t_all_presets_have_evidence_and_no_provider_dependency():
    data=radio.assess(snapshot(), {'setup':{}, 'service':None})
    assert set(data['answers'])=={'fuel','pace','tyres','status'}
    assert all(isinstance(v,str) and v for v in data['answers'].values())
    assert data['fuel']['consumption_l']==3 and data['fuel']['range_laps']==5
    assert data['fuel']['sample_ids']==['L2','L3','L4','L5','L6']
    assert data['fuel']['session_average_l']==7
    assert data['pace']['state']=='slower' and data['pace']['delta_s']==1
    assert data['pace']['baseline_ids']==['L1','L2','L3']
    assert data['pace']['recent_ids']==['L4','L5','L6']
    assert 'may' in data['answers']['pace'].lower()
    assert data['tyres']['temperatures_c']==[80,81,82,83]
    assert 'wear' in ' '.join(data['uncertainties']).lower()


def t_reserve_partial_lap_finish_and_warning():
    snap=snapshot();snap['instruments']['fuel_l']=5
    result=radio.assess(snap, {'setup':{'fuel_reserve_l':2,'laps':7}})['fuel']
    assert result['range_laps']==round(5/3,3) and result['usable_laps']==1
    assert result['remaining_race_laps']==.5, 'current trace length must not imply a complete lap'
    assert result['finish']=='supported' and result['severity']=='normal'
    result=radio.assess(snap, {'setup':{'fuel_reserve_l':2,'laps':10}})['fuel']
    assert result['severity']=='urgent' and result['finish']=='short'
    snap['current']['observed_from_line']=False
    result=radio.assess(snap, {'setup':{'laps':7}})['fuel']
    assert result['finish']=='unknown' and result['severity']=='warning'
    snap['current']['lap']=8
    assert radio.assess(snap, {'setup':{'laps':7}})['fuel']['finish']=='complete'


def t_pace_requires_repeated_comparable_evidence():
    assert radio.assess(snapshot((90,90,90,90,90,130)))['pace']['state']=='stable'
    assert radio.assess(snapshot((90,94,86,91,94,89)))['pace']['state']=='stable'
    assert radio.assess(snapshot((90,90,90,89,89,89)))['pace']['state']=='faster'
    assert radio.assess(snapshot((90,90,91)))['pace']['state']=='insufficient'
    for field,value in (('session_id','other'),('car_id',2),('track_id','other'),('compound','RS'),
                        ('strategy',{'fuel_map':6}),('after_service',True)):
        snap=snapshot()
        if field=='strategy':snap['current']['strategy']={'fuel_map':1}
        snap['laps'][-3][field]=value
        assert radio.assess(snap)['pace']['state']=='insufficient', (field,radio.assess(snap)['pace'])
    snap=snapshot();snap['laps'][2]['analysis_role']='pit'
    assert radio.assess(snap)['pace']['state']=='insufficient'


def t_service_resets_evidence_without_inventing_tyres_or_wear():
    snap=snapshot()
    result=radio.assess(snap, {'setup':{'starting_compound':'RS'},
                             'service':{'lap_id':'L4','compound':'RH','confirmed_at':100}})
    assert result['tyres']['compound']=='RH' and result['pace']['state']=='insufficient'
    snap['current']['compound']=None
    for row in snap['laps']:row['compound']=None
    result=radio.assess(snap)
    assert result['pace']['state']=='slower'
    assert result['tyres']['compound'] is None
    assert not any(word in result['answers']['pace'].lower() for word in ('box now','wear percent','ideal temperature'))
    assert any('traffic' in word.lower() for word in result['uncertainties'])


def t_bad_or_stale_measurements_are_explicitly_unavailable():
    for state in ('paused','loading','menu','stale','delayed','waiting'):
        snap=snapshot();snap['receipt']['state']=state
        result=radio.assess(snap)
        assert not result['live'] and result['fuel']['severity']=='normal'
        assert result['fuel']['range_laps'] is None and result['tyres']['temperatures_c']==[None]*4
        assert 'unavailable' in result['answers']['fuel'].lower()
    snap=snapshot();snap['replay']['active']=True
    assert not radio.assess(snap)['live']
    for value in (None,0,-1,float('nan'),float('inf'),True,1e-320):
        snap=snapshot()
        for row in snap['laps']:row['fuel_burn_l']=value;row['fuel_used_l']=value
        result=radio.assess(snap)
        assert result['fuel']['range_laps'] is None
    assert radio.assess({})['pace']['state']=='insufficient'


def t_current_service_and_authoritative_zero_burn_do_not_reuse_old_estimates():
    snap=snapshot();snap['current']['after_service']=True
    result=radio.assess(snap)
    assert result['pace']['state']=='insufficient' and result['fuel']['range_laps'] is None
    snap=snapshot()
    for row in snap['laps']:row['fuel_burn_l']=0;row['fuel_used_l']=3
    assert radio.assess(snap)['fuel']['range_laps'] is None
    snap=snapshot();snap['instruments']['fuel_l']=None
    assert radio.assess(snap)['fuel']['state']=='unavailable'


def t_timed_race_requires_observed_start_and_publishes_final_lap_assumption():
    snap=snapshot((90,90,90,90,90,90))
    result=radio.assess(snap, {'setup':{'duration_min':10}})['fuel']
    assert result['remaining_race_laps']==round(15/90+1,3)
    assert result['finish_assumption']=='one extra lap after timer expiry'
    assert result['finish']=='estimated_supported'
    snap['laps']=snap['laps'][1:]
    assert radio.assess(snap, {'setup':{'duration_min':10}})['fuel']['finish']=='unknown'


def t_limits_are_validated_and_pure_inputs_are_unchanged():
    snap=snapshot();before=copy.deepcopy(snap)
    result=radio.assess(snap, limits={'fuel_warning_laps':6,'fuel_urgent_laps':2})
    assert result['fuel']['severity']=='warning' and snap==before
    for limits in ({'fuel_warning_laps':1,'fuel_urgent_laps':2}, {'fuel_urgent_laps':True},
                   {'fuel_warning_laps':float('nan')}, {'recent_laps':0}, {'unknown':1}):
        try:radio.assess(snap,limits=limits)
        except ValueError:pass
        else:raise AssertionError('Invalid limits accepted: '+str(limits))


def t_real_replay_supplies_production_snapshot_facts():
    import tempfile
    import gt7_recording
    import gt7_telemetry
    import race_engineer_live
    from test_gt7_laps import FakeCars, FakeTracks, write_circle_recording
    with tempfile.TemporaryDirectory() as d:
        path=write_circle_recording(d)
        with race_engineer_live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(),
                                      queue_max=4096, clock=lambda:100.0) as store:
            start=None
            for wall,_kind,plain in gt7_recording.Recording(path).packets():
                if start is None:start=wall
                pkt=gt7_telemetry.parse_packet(plain)._replace(fuel_level=60-(wall-start)/10)
                assert store.put(pkt, wall)
            assert store.flush(10)
            snap=store.snapshot()
            result=radio.assess(snap)
            assert result['live'] and result['fuel']['consumption_l'] > 0
            assert result['fuel']['current_l']==snap['instruments']['fuel_l']
            assert result['fuel']['range_laps'] > 0
            assert set(result['fuel']['sample_ids']) <= {r['id'] for r in snap['laps'] if r['comparison_eligible']}
            assert all(result['answers'][preset] for preset in radio.PRESETS)


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

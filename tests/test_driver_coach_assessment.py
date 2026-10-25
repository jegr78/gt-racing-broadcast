#!/usr/bin/env python3
"""Grounded live coaching experiments include exit and following-passage evidence."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import driver_coach_assessment as coach


def definition(reviewed=True):
    return dict(fingerprint='definition-v1', reviewed=reviewed, corners=[
        dict(id='turn-a', number=1, name='First bend', direction='right', entry_m=50, apex_m=125, exit_m=180),
        dict(id='turn-b', number=2, name='Second bend', direction='left', entry_m=220, apex_m=250, exit_m=300)],
        combinations=[])


def lap(ident='own', reference=False, corner_gain=0, downstream_loss=0):
    trace=[]
    for d in range(0, 401, 5):
        elapsed=d*.02+corner_gain*max(0,min(1,(d-50)/130))+downstream_loss*max(0,min(1,(d-180)/120))
        trace.append(dict(d=d,t=elapsed,speed_kmh=60+abs(d-120)*.3,
                          brake=70 if (55 if reference else 65)<=d<=(130 if reference else 150) else 0,
                          throttle=100 if d>=(145 if reference else 165) else 0,
                          steer_deg=4 if reference else 12,gear=4 if reference else 3,
                          x=float(d),z=0.0 if reference else 2.0))
    return dict(id=ident,session_id='live',session=1,rec='live',lap=2,car_id=1,track_id='ring',layout='Full',
                state='confirmed',comparison_eligible=True,pace_eligible=True,capture_complete=True,
                trace_complete=True,time_valid=True,data_quality='ok',analysis_role='regular',
                after_service=False,compound=None,strategy={},context_settings={},
                settings_confirmed=False,definition_fingerprint='definition-v1',trace=trace,
                length_m=400,time_s=trace[-1]['t'])


def assess(own=None, reference=None, **kwargs):
    return coach.assess(own or lap(),reference or lap('ref',True),definition=definition(),**kwargs)


def t_observed_channels_produce_grounded_experiments_with_exact_evidence():
    result=assess()
    assert result['status']=='ready'
    skills={f['skill'] for f in result['findings']}
    assert {'braking','throttle','steering','gear','line'} <= skills
    brake=next(f for f in result['findings'] if f['metric']=='brake_last_m')
    assert brake['observed']==150 and brake['reference']==130
    assert brake['unit']=='m' and brake['delta']==20
    assert brake['label']=='Turn 1: First bend' and brake['reviewed']
    assert all(brake[k] for k in ('observation','interpretation','action','check'))
    assert brake['check']['end_m']==300 and brake['check']['available']
    assert all(f['evidence'] for f in result['findings'])
    text=' '.join(f['action'] for f in result['findings']).lower()
    assert not any(word in text for word in ('10 m','20 m','optimal','ideal','hydraulic','guaranteed'))
    assert 'traffic' in ' '.join(result['unknown']).lower()
    assert 'trace' not in result and 'recording_path' not in result


def t_missing_channels_do_not_turn_into_observed_steering_or_braking():
    own=lap()
    for point in own['trace']:
        point.pop('steer_deg')
    own['trace'][20]['brake']=None
    result=assess(own)
    assert not any(f['skill'] in ('steering','braking') for f in result['findings'])
    assert any('steering' in u.lower() for u in result['unknown'])
    assert any(f['skill']=='throttle' for f in result['findings'])


def t_faster_corner_and_slower_following_passage_is_a_tradeoff_not_progress():
    own=lap(corner_gain=-.3,downstream_loss=.6)
    result=assess(own)
    finding=next(f for f in result['findings'] if f['section_id']=='turn-a')
    check=finding['check']
    assert round(check['corner_delta_s'],3)==-.3
    assert round(check['downstream_delta_s'],3)==.6
    assert round(check['net_delta_s'],3)==.3
    assert check['result']=='tradeoff'
    faster=assess(lap(corner_gain=-.3))
    assert next(f for f in faster['findings'] if f['section_id']=='turn-a')['check']['result']=='provisional_gain'


def t_exit_speed_and_complete_following_evidence_are_required_for_a_gain():
    own=lap(corner_gain=-.3)
    own['trace'][36]['speed_kmh']=40
    result=assess(own)
    assert all(f['check']['result']!='provisional_gain' for f in result['findings'] if f['section_id']=='turn-a')
    own['trace'][36]['speed_kmh']=None
    result=assess(own)
    assert not any(f.get('section_id')=='turn-a' for f in result['findings'])


def t_connected_combinations_extend_the_check_and_unlabelled_sections_are_safe():
    doc=definition()
    doc['corners'].append(dict(id='turn-c',number=3,name='Third bend',direction='right',entry_m=325,apex_m=350,exit_m=375))
    doc['combinations']=[{'id':'combo','corner_ids':['turn-a','turn-b','turn-c']}]
    result=coach.assess(lap(),lap('ref',True),definition=doc)
    assert next(f for f in result['findings'] if f['section_id']=='turn-a')['check']['end_m']==375
    doc['corners'][0].pop('number');doc['corners'][0].pop('direction')
    result=coach.assess(lap(),lap('ref',True),definition=doc)
    assert all(not f['reviewed'] for f in result['findings'] if f['section_id']=='turn-a')


def t_provisional_geometry_has_qualified_labels_and_definition_changes_fail_closed():
    result=coach.assess(lap(),lap('ref',True),definition=definition(False))
    assert result['findings']
    assert all(not f['reviewed'] for f in result['findings'])
    assert all('provisional' in f['label'].lower() for f in result['findings'])
    assert all('turn 1' not in f['observation'].lower() for f in result['findings'])
    changed=definition();changed['fingerprint']='another-version'
    result=coach.assess(lap(),lap('ref',True),definition=changed)
    assert not any(f.get('section_id') for f in result['findings'])
    assert any('definition' in u.lower() for u in result['unknown'])


def t_repeated_support_precedes_one_large_single_lap_difference_and_noise_is_filtered():
    earlier=[lap('earlier-'+str(n)) for n in range(2)]
    for row in earlier:
        for point in row['trace']:point['steer_deg']=4
    own=lap()
    for point in own['trace']:point['steer_deg']=100
    result=assess(own,personal_laps=earlier)
    assert result['findings'][0]['support']>=3
    assert result['findings'][0]['skill']!='steering'
    tiny=lap('tiny',True)
    for point in tiny['trace']:point['steer_deg']=4.1
    result=assess(tiny)
    assert not any(f['skill']=='steering' for f in result['findings'])


def t_no_external_target_and_no_geometry_still_has_a_usable_personal_hint():
    own=lap();other=lap('earlier',True)
    result=coach.assess(own,definition=None,personal_laps=[other])
    assert result['summary'] and result['status'] in ('ready','insufficient')
    assert not any(f.get('section_id') for f in result['findings'])
    assert all(f['skill']=='consistency' for f in result['findings'])
    assert 'optimal' not in result['summary'].lower()
    own['analysis_role']='warmup';result=coach.assess(own,personal_laps=[other])
    assert result['status']=='unsuitable' and not result['findings']


def t_current_lap_cannot_be_its_own_target_and_missing_session_identity_is_unusable():
    own=lap();earlier=lap('earlier',True)
    result=coach.assess(own,own,definition=definition(),personal_laps=[earlier])
    assert result['reference_id']=='earlier'
    assert any(f.get('section_id') for f in result['findings'])
    own.pop('session_id');own.pop('rec');own.pop('session')
    result=coach.assess(own,earlier,definition=definition())
    assert result['status']=='unsuitable' and not result['findings']


def t_incompatible_and_sparse_or_invalid_traces_cannot_support_corner_claims():
    for changes in ({'car_id':2},{'track_id':'other'},{'layout':'Reverse'},{'stint_id':'fresh'},
                    {'compound':'RM'},{'strategy':{'fuel_map':2}},{'definition_fingerprint':'different'}):
        own=lap()
        if 'strategy' in changes:own['strategy']={'fuel_map':1}
        reference=lap('ref',True);reference.update(changes)
        result=assess(own,reference)
        assert not any(f.get('section_id') for f in result['findings']),changes
        assert result['reference_id'] is None,changes
    for damage in ('gap','nan','reverse','local_order','time_order','distance_order'):
        own=lap()
        if damage=='gap':own['trace']=own['trace'][::10]
        if damage=='nan':own['trace'][2]['t']=float('nan')
        if damage=='reverse':own['trace'].reverse()
        if damage=='local_order':own['trace'][1],own['trace'][2]=own['trace'][2],own['trace'][1]
        if damage=='time_order':own['trace'][2]['t']=own['trace'][1]['t']-.01
        if damage=='distance_order':own['trace'][2]['d']=own['trace'][1]['d']
        result=assess(own)
        assert not any(f.get('section_id') for f in result['findings']),damage


def t_malformed_identity_and_bounded_inputs_fail_closed_without_corner_claims():
    for change in ({'car_id':True},{'session_id':[]},{'track_id':{}},{'strategy':[]}):
        own=lap();own.update(change)
        assert coach.assess(own)['status']=='unsuitable'
    own=lap();own['trace']=[own['trace'][0]]*(coach.TRACE_MAX+1)
    assert not any(f.get('section_id') for f in assess(own)['findings'])
    doc=definition();doc['combinations']=[{'corner_ids':None}]
    result=coach.assess(lap(),lap('ref',True),definition=doc)
    assert len(result['findings'])<=coach.FINDINGS_MAX


def t_truncated_connected_context_cannot_claim_an_early_provisional_gain():
    doc=definition()
    first=doc['corners'][0]
    doc['corners']=[dict(first,id='c'+str(i),number=i+1,exit_m=180+i*.001) for i in range(coach.CORNERS_MAX)]
    doc['corners'].append(dict(first,id='far',number=coach.CORNERS_MAX+1,entry_m=325,apex_m=350,exit_m=375))
    doc['combinations']=[dict(corner_ids=['c0','far'])]
    own=lap(corner_gain=-.3)
    for point in own['trace']:point['t']+=.8*max(0,min(1,(point['d']-300)/75))
    own['time_s']=own['trace'][-1]['t']
    result=coach.assess(own,lap('ref',True),definition=doc)
    assert not any(f.get('section_id') for f in result['findings'])
    assert any('budget' in message.lower() for message in result['unknown'])
    complete=dict(doc,corners=[doc['corners'][0],doc['corners'][-1]])
    result=coach.assess(own,lap('ref',True),definition=complete)
    check=next(f for f in result['findings'] if f['section_id']=='c0')['check']
    assert check['end_m']==375 and round(check['net_delta_s'],3)==.5 and check['result']=='tradeoff'
    doc=definition();doc['combinations']=[dict(id='group-'+str(i),corner_ids=['turn-b']) for i in range(coach.CORNERS_MAX)]+[dict(corner_ids=['turn-a','turn-b'])]
    result=coach.assess(lap(corner_gain=-.3),lap('ref',True),definition=doc)
    assert not any(f.get('section_id') for f in result['findings'])
    doc=definition();doc['combinations']=[dict(corner_ids=['turn-a','unknown'])]
    result=coach.assess(lap(corner_gain=-.3),lap('ref',True),definition=doc)
    assert not any(f.get('section_id') for f in result['findings'])
    doc=definition();doc['corners']=[dict(first,id='section-'+str(i)) for i in range(coach.CORNERS_MAX+1)]
    result=coach.assess(lap(corner_gain=-.3),lap('ref',True),definition=doc)
    assert not any(f.get('section_id') for f in result['findings'])


def t_extreme_finite_coordinates_leave_line_unavailable_without_losing_timing():
    own,ref=lap(),lap('ref',True)
    for point in own['trace']:point['x']=10**308
    for point in ref['trace']:point['x']=-(10**308)
    try:result=assess(own,ref)
    except (OverflowError,ValueError) as error:raise AssertionError('Line arithmetic must degrade without losing other evidence') from error
    assert not any(f['skill']=='line' for f in result['findings'])
    assert any(f['skill']=='braking' for f in result['findings'])


def t_trace_cohort_and_result_budgets_are_observable_with_otherwise_usable_data():
    own=lap()
    own['trace']=[dict(d=i*.25,t=i*.01,speed_kmh=100) for i in range(coach.TRACE_MAX+1)]
    own['time_s']=own['trace'][-1]['t'];own['length_m']=own['trace'][-1]['d']
    assert not any(f.get('section_id') for f in assess(own)['findings'])
    own=lap()
    previous=[lap('prior-'+str(i)) for i in range(coach.COHORT_MAX+3)]
    result=assess(own,personal_laps=previous)
    assert all(f['support']<=coach.COHORT_MAX for f in result['findings'])
    doc=definition();first=doc['corners'][0]
    doc['corners']=[dict(first,id='section-'+str(i),number=i+1,entry_m=50+i*5,apex_m=125+i*5,exit_m=180+i*5) for i in range(8)]
    result=coach.assess(own,lap('ref',True),definition=doc)
    assert len(result['findings'])==coach.FINDINGS_MAX


def t_unsuitable_lap_flags_and_reference_identity_do_not_produce_technique_scores():
    for changes in ({'state':'provisional'},{'comparison_eligible':False},{'capture_complete':False},
                    {'trace_complete':False},{'time_valid':False},{'data_quality':'interrupted'},
                    {'analysis_role':'warmup'},{'after_service':True},{'time_s':0},
                    {'time_s':float('nan')},{'time_s':True}):
        own=lap();own.update(changes)
        result=assess(own)
        assert result['status']=='unsuitable' and not result['findings'],changes
    own,ref=lap(),lap('ref',True);own['track_id']=None;ref['track_id']=None;ref['session_id']='earlier'
    assert assess(own,ref)['reference_id'] is None
    own,ref=lap(),lap('ref',True);own['compound']='RM'
    assert assess(own,ref)['reference_id'] is None


def t_production_live_store_rows_provide_personal_consistency_without_an_ideal_target():
    from test_gt7_laps import FakeCars,FakeTracks,write_circle_recording
    from test_race_engineer_live import feed
    from race_engineer_live import Store
    with tempfile.TemporaryDirectory() as d:
        path=write_circle_recording(d)
        with Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),queue_max=4096) as store:
            feed(store,path)
            rows=[store.detail(r['id'])['lap'] for r in store.snapshot()['laps'] if r['comparison_eligible']]
            result=coach.assess(rows[-1],personal_laps=rows[:-1])
            assert result['summary'] and result['lap_id']==rows[-1]['id']
            assert all(f['skill']=='consistency' for f in result['findings'])


def t_production_corner_metrics_feed_coaching_checks_from_a_reviewed_definition():
    from test_gt7_laps import FakeCars,FakeTracks,write_circle_recording
    from test_gt7_track_definitions import definition as authored
    from test_race_engineer_live import feed
    from race_engineer_live import Store
    import gt7_track_definitions as definitions
    with tempfile.TemporaryDirectory() as d:
        path=write_circle_recording(d)
        definitions.Store(d,str(Path(d)/'empty-bundle')).save(authored(),0)
        with Store(lambda:True,tracks=FakeTracks(),cars=FakeCars(),queue_max=4096,runtime_base=d) as store:
            feed(store,path)
            rows=[store.detail(r['id'])['lap'] for r in store.snapshot()['laps'] if r['comparison_eligible']]
            own,ref=rows[0],rows[1]
            result=coach.assess(own,ref,definition=own['track_definition'],personal_laps=rows)
            assert own['corner_metrics'] and own['definition_fingerprint']
            assert any(f['skill']=='pace' and f['check']['available'] for f in result['findings'])


def t_indexed_trace_cannot_append_points_without_updating_its_distance_index():
    trace=coach._trace(lap());same=coach._trace(lap())
    assert trace==same and len(trace)==81 and trace[0]['d']==0
    assert coach._duration(trace,50,300)==5
    assert coach._window(trace,50,60)[0]['d']==45
    try:trace.append(dict(d=405,t=8.1))
    except AttributeError:pass
    else:raise AssertionError('Indexed trace allowed an unsynchronized point append')
    try:trace.distances[0]=-1
    except TypeError:pass
    else:raise AssertionError('Indexed trace allowed direct index mutation')


if __name__=='__main__':
    for name,fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):fn();print('PASS',name)

#!/usr/bin/env python3
"""A coach focus survives attempts and distinguishes repeatability from an outlier."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import driver_coach_focus as coach
import driver_coach_calibration as calibration


def lap(n, seconds=90, **changes):
    row=dict(id='lap-'+str(n),lap=n,session_id='live',car_id=1,track_id='ring',layout='Full',
             state='confirmed',time_s=seconds,time_valid=True,comparison_eligible=True,
             capture_complete=True,trace_complete=True,data_quality='ok',analysis_role='regular',
             after_service=False,definition_fingerprint='definition',compound=None,stint_id=None,
             strategy={},context_settings={})
    row.update(changes)
    return row


def finding(ident='turn/brake', skill='braking', support=3, strength=1, net=-.2, downstream=0, exit_delta=0):
    return dict(id=ident,section_id='turn',skill=skill,support=support,strength=strength,
                observation='Measured pedal transition differs.',interpretation='Cause is unknown.',
                action='Consider repeating a smooth transition.',label='Provisional section',reviewed=False,
                check=dict(available=True,start_m=50,end_m=300,own_s=10+net,reference_s=10,
                           net_delta_s=net,corner_delta_s=net-downstream,downstream_delta_s=downstream,
                           exit_speed_delta_kmh=exit_delta,screen_s=.05,result='provisional_gain'),
                evidence=[{'id':'evidence/lap/metric','lap_id':'lap-2','value':10,'unit':'s'}])


def assessment(row, findings=None, status='ready', reference='lap-1'):
    facts=copy.deepcopy(findings if findings is not None else [finding()])
    for fact in facts:
        for evidence in fact.get('evidence',[]):evidence['lap_id']=row['id']
    return dict(status=status,lap_id=row['id'],reference_id=reference,
                findings=facts,summary='Completed suitable lap.')


def ready(objective='balanced', times=(90,92)):
    cal=calibration.Calibration();cal.start(dict(profile='solo',subject='driver'),objective=objective)
    baseline=[lap(1,times[0]),lap(2,times[1])]
    for row in baseline:cal.ingest(row)
    engine=coach.Focus()
    state=engine.observe(baseline[-1],assessment(baseline[-1]),cal.snapshot(),baseline)
    cal.begin_focus()
    return engine,cal,baseline,state


def attempt(engine,cal,n,seconds=89,findings=None,**changes):
    row=lap(n,seconds,**changes);snap=cal.ingest(row)
    return engine.observe(row,assessment(row,findings),snap,cal.baseline_laps())


def rejects(fn, code):
    try:fn()
    except calibration.CoachError as error:assert error.code==code,(error.code,code)
    else:raise AssertionError('Expected focus rejection: '+code)


def t_default_balanced_freezes_one_focus_reference_and_original_baseline():
    engine,cal,baseline,state=ready()
    assert state['objective']=='balanced' and state['phase']=='provisional'
    assert state['focus']['id']=='turn/brake' and state['attempts']==[]
    frozen=copy.deepcopy(state['baseline']);reference=state['reference_id']
    state=attempt(engine,cal,3,89,[finding('other/steer','steering',9,3),finding()])
    assert state['focus']['id']=='turn/brake' and len(state['attempts'])==1
    assert state['baseline']==frozen and state['reference_id']==reference=='lap-1'
    assert cal.reference()['id']=='lap-1' and baseline[0]['time_s']==90


def t_recurring_support_beats_larger_one_off_and_objective_breaks_ties():
    engine,cal,baseline,state=ready()
    engine=coach.Focus();row=baseline[-1]
    facts=[finding('isolated','steering',1,3),finding('repeated','braking',3,1)]
    state=engine.observe(row,assessment(row,facts),cal.snapshot(),baseline)
    assert state['focus']['id']=='repeated'
    engine=coach.Focus();cal.set_objective('pace')
    for row in (lap(3,92),lap(4,90)):cal.ingest(row)
    facts=[finding('brake','braking',3,3),finding('pace','pace',3,1)]
    state=engine.observe(row,assessment(row,facts,reference='lap-4'),cal.snapshot(),cal.baseline_laps())
    assert state['focus']['id']=='pace'


def t_one_faster_outlier_is_provisional_and_three_repeated_balanced_results_complete():
    engine,cal,_,_=ready()
    state=attempt(engine,cal,3,89)
    assert state['phase']=='active' and state['progress']['result']=='provisional'
    state=attempt(engine,cal,4,90,[finding(net=0)])
    assert state['phase']=='active' and not state['progress']['pace_improved']
    for n in (5,6,7):state=attempt(engine,cal,n,89)
    assert state['phase']=='completed' and state['progress']['pace_improved']
    assert state['progress']['consistency_improved'] and state['progress']['sample_count']==3
    assert 'learning' not in state['progress']['text'].lower()


def t_pace_checks_reject_hidden_following_or_exit_losses_and_ignore_result_label():
    for damaged in (finding(net=-.2,downstream=.3),finding(net=-.2,exit_delta=-5),finding(net=.3)):
        engine,cal,_,_=ready('pace')
        for n in (3,4,5):state=attempt(engine,cal,n,89,[damaged])
        assert state['phase']!='completed' and not state['progress']['pace_improved'],damaged
    engine,cal,_,_=ready('pace')
    for n,seconds in ((3,89),(4,91),(5,89)):
        state=attempt(engine,cal,n,seconds)
    assert state['phase']=='completed', 'Pace was incorrectly made conditional on reduced lap variation'


def t_consistency_is_reduced_variation_and_zero_starting_variation_is_not_improvement():
    engine,cal,_,_=ready('consistency')
    for n in (3,4,5):state=attempt(engine,cal,n,91,[finding(net=.2)])
    assert state['phase']=='completed' and state['progress']['consistency_improved']
    assert not state['progress']['pace_improved']
    engine,cal,_,_=ready('consistency',(90,90))
    for n in (3,4,5):state=attempt(engine,cal,n,90)
    assert state['phase']!='completed' and not state['progress']['consistency_improved']
    assert state['progress']['result']=='repeatability_maintained'


def t_unsuitable_duplicate_out_of_order_and_missing_focused_check_do_not_score():
    engine,cal,_,state=ready();frozen=state['focus']
    state=attempt(engine,cal,3,89,analysis_role='warmup')
    assert state['focus']==frozen and state['attempts']==[]
    state=attempt(engine,cal,4,89);count=len(state['attempts'])
    for n in (4,3):
        state=attempt(engine,cal,n,89)
        assert len(state['attempts'])==count
    missing=finding('other','steering');missing['section_id']='other-section'
    state=attempt(engine,cal,5,89,[missing])
    assert len(state['attempts'])==count and state['focus']==frozen
    assert state['review'] and state['progress']['result']=='evidence_unavailable'


def t_skill_skip_and_keep_are_explicit_and_old_epoch_controls_fail():
    engine,cal,_,state=ready();epoch=state['epoch']
    engine.control('skill',epoch,skill='steering')
    state=attempt(engine,cal,3,89,[finding(),finding('steer','steering')])
    assert state['focus']['id']=='steer'
    engine.control('skip',epoch)
    state=attempt(engine,cal,4,89,[finding('steer','steering'),finding('steer-2','steering')])
    assert state['focus']['id']=='steer-2'
    engine.control('keep',epoch)
    for n in (5,6,7):state=attempt(engine,cal,n,89,[finding('steer-2','steering')])
    assert state['phase']=='retained' and state['progress']['result']=='repeated_improvement'
    assert any(event['state']=='skipped' for event in state['history'])
    rejects(lambda:engine.control('skip','old-epoch'),'conflict')
    rejects(lambda:engine.control('skill',epoch,skill='optimal'),'invalid_skill')
    rejects(lambda:engine.control('invented',epoch),'invalid_control')


def t_epoch_and_reference_changes_invalidate_old_focus_with_provenance():
    engine,cal,_,state=ready();old=state['epoch'];base=state['baseline']['id']
    attempt(engine,cal,3,89)
    cal.set_objective('pace')
    row=lap(4,89);snap=cal.ingest(row)
    state=engine.observe(row,assessment(row),snap,cal.baseline_laps())
    assert state['epoch']!=old and state['focus'] is None and state['attempts']==[]
    assert any(e['state']=='invalidated' and e['baseline_id']==base for e in state['history'])
    rejects(lambda engine=engine,old=old:engine.control('keep',old),'conflict')
    engine,cal,_,state=ready();old=state['epoch']
    snap=cal.select_reference(lap(20,88,session_id='archive'),authorize=lambda:True)
    row=lap(3,89);snap=cal.ingest(row)
    state=engine.observe(row,assessment(row,reference='lap-20'),snap,cal.baseline_laps())
    assert state['epoch']!=old and state['attempts']==[]


def t_changed_check_geometry_reference_or_nonfinite_values_cannot_score_success():
    for field,value in (('end_m',350),('reference_s',11),('net_delta_s',float('nan')),
                        ('own_s',float('inf')),('screen_s',-1)):
        engine,cal,_,_=ready('pace');bad=finding();bad['check'][field]=value
        for n in (3,4,5):state=attempt(engine,cal,n,89,[bad])
        assert state['phase']!='completed' and not state['attempts'],(field,state)
    engine,cal,_,_=ready('pace');row=lap(3,89);snap=cal.ingest(row)
    state=engine.observe(row,assessment(row,reference='foreign'),snap,cal.baseline_laps())
    assert not state['attempts']


def t_general_personal_focus_works_without_geometry_or_external_target():
    engine,cal,baseline,_=ready('pace');engine=coach.Focus();row=baseline[-1]
    state=engine.observe(row,assessment(row,[]),cal.snapshot(),baseline)
    assert state['focus']['skill']=='consistency'
    for n in (3,4,5):state=attempt(engine,cal,n,89,[])
    assert state['phase']=='completed' and state['progress']['pace_improved']
    assert 'cause' in state['progress']['text'].lower()


def t_repeated_regression_is_reported_but_focus_remains_available():
    engine,cal,_,_=ready('pace')
    for n in (3,4,5):state=attempt(engine,cal,n,93,[finding(net=.4)])
    assert state['phase']=='regression' and state['focus']['id']=='turn/brake'
    for n in (6,7,8):state=attempt(engine,cal,n,89)
    assert state['phase']=='completed'


def t_compact_snapshots_are_copied_and_state_budgets_do_not_grow():
    engine,cal,_,state=ready('consistency',(90,90))
    state['focus']['action']='tampered';state['baseline']['lap_ids'].clear()
    assert engine.snapshot()['focus']['action']!='tampered' and engine.snapshot()['baseline']['lap_ids']
    for n in range(3,180):state=attempt(engine,cal,n,90)
    assert len(state['attempts'])<=coach.ATTEMPTS_MAX and state['seen_count']<=coach.SEEN_MAX
    assert len(state['history'])<=coach.HISTORY_MAX
    text=json.dumps(state,allow_nan=False)
    assert 'trace' not in text and len(text)<64000


def t_mismatched_assessment_or_owner_cannot_append_attempts():
    engine,cal,_,_=ready();row=lap(3,89);snap=cal.ingest(row)
    bad=assessment(row);bad['lap_id']='another-lap'
    state=engine.observe(row,bad,snap,cal.baseline_laps())
    assert not state['attempts']
    snap['owner']['subject']='another-driver'
    state=engine.observe(row,assessment(row),snap,cal.baseline_laps())
    assert not state['attempts'] and state['focus'] is None


def t_invalid_or_oversized_findings_fall_back_without_retaining_private_extra_fields():
    engine,cal,baseline,_=ready();engine=coach.Focus();row=baseline[-1]
    bad=finding();bad['skill']='hydraulic-pressure';bad['trace']=['private']
    state=engine.observe(row,assessment(row,[bad]),cal.snapshot(),baseline)
    assert state['focus']['skill']=='consistency' and 'private' not in json.dumps(state)
    engine=coach.Focus();bad=finding();bad['action']='x'*10000
    state=engine.observe(row,assessment(row,[bad]),cal.snapshot(),baseline)
    assert state['focus']['skill']=='consistency' and len(json.dumps(state))<64000


def t_unavailable_calibration_invalidates_focus_and_old_controls():
    for damaged in (None,{},dict(active=False)):
        engine,cal,_,state=ready();old=state['epoch'];row=lap(3,89)
        state=engine.observe(row,assessment(row),damaged,cal.baseline_laps())
        assert state['focus'] is None and state['attempts']==[]
        rejects(lambda engine=engine,old=old:engine.control('keep',old),'conflict')


def t_completed_focus_advances_to_another_exercise_unless_kept():
    engine,cal,_,_=ready('pace')
    for n in (3,4,5):state=attempt(engine,cal,n,89)
    assert state['phase']=='completed'
    state=attempt(engine,cal,6,89,[finding(),finding('next','throttle')])
    assert state['focus']['id']=='next' and not state['attempts']
    assert any(e['state']=='completed' for e in state['history'])


def t_malformed_current_context_or_baseline_cannot_select_or_score():
    for change in ({'car_id':True},{'strategy':[]},{'context_settings':[]},{'settings_confirmed':'yes'}):
        engine,cal,_,_=ready();row=lap(3,89,**change)
        snap=cal.snapshot();snap['accepted']=True
        state=engine.observe(row,assessment(row),snap,cal.baseline_laps())
        assert not state['attempts']
    engine,cal,_,_=ready();engine=coach.Focus();row=lap(3,89);snap=cal.ingest(row)
    baseline=cal.baseline_laps();baseline[0]['context_settings']=[]
    state=engine.observe(row,assessment(row),snap,baseline)
    assert state['focus'] is None


def t_correcting_the_original_metric_does_not_hide_a_measured_same_section_gain():
    engine,cal,_,_=ready('pace')
    for n in (3,4,5):
        state=attempt(engine,cal,n,89,[finding('turn/window_time_s','pace')])
    assert state['phase']=='completed' and state['focus']['id']=='turn/brake'
    assert state['progress']['pace_improved']


def t_admission_and_current_context_guards_do_not_accept_unsuitable_or_foreign_laps():
    for change in ({'state':'provisional'},{'comparison_eligible':False},{'capture_complete':False},
                   {'trace_complete':False},{'time_valid':False},{'data_quality':'interrupted'},
                   {'analysis_role':'pit'},{'after_service':True},{'time_s':0},{'time_s':float('nan')},
                   {'lap':True},{'id':[]},{'session_id':'other'},{'car_id':2},{'compound':'RM'}):
        engine,cal,_,_=ready();row=lap(3,89,**change);snap=cal.snapshot();snap['accepted']=True
        state=engine.observe(row,assessment(dict(row,id='lap-3')),snap,cal.baseline_laps())
        assert not state['attempts'],change
    engine,cal,_,_=ready();row=lap(3,89);snap=cal.snapshot();snap['accepted']=False
    assert not engine.observe(row,assessment(row),snap,cal.baseline_laps())['attempts']
    snap['accepted']=True
    assert not engine.observe(row,assessment(row,status='unsuitable'),snap,cal.baseline_laps())['attempts']


def t_evidence_and_screen_are_numeric_and_cannot_be_replaced_by_success_prose():
    for change in ({'available':False},{'own_s':True},{'net_delta_s':-9},
                   {'downstream_delta_s':7},{'start_m':-1},{'end_m':10**400}):
        engine,cal,_,_=ready('pace');bad=finding();bad['check'].update(change)
        for n in (3,4,5):state=attempt(engine,cal,n,89,[bad])
        assert state['phase']!='completed' and not state['attempts'],change
    engine,cal,_,_=ready('pace');bad=finding(net=-.001);bad['check']['screen_s']=.00001
    for n in (3,4,5):state=attempt(engine,cal,n,89,[bad])
    assert state['phase']!='completed' and not state['progress']['pace_improved']
    for malformed in ({'evidence':[]},{'support':True},{'strength':100},{'section_id':[]},
                      {'evidence':[{'id':'x','lap_id':'other','unit':'s','value':float('nan')}]}):
        engine,cal,baseline,_=ready();engine=coach.Focus();row=baseline[-1]
        bad=finding();bad.update(malformed)
        state=engine.observe(row,assessment(row,[bad]),cal.snapshot(),baseline)
        assert state['focus']['skill']=='consistency',malformed


def t_findings_and_dismissals_have_explicit_bounds_without_partial_failed_control():
    engine,cal,baseline,_=ready();engine=coach.Focus();row=baseline[-1]
    state=engine.observe(row,assessment(row,[finding(str(n)) for n in range(coach.FINDINGS_MAX+1)]),cal.snapshot(),baseline)
    assert state['focus']['skill']=='consistency'
    engine,cal,_,state=ready();epoch=state['epoch']
    for n in range(3,3+coach.DISMISSED_MAX):
        engine.control('skip',epoch)
        state=attempt(engine,cal,n,90,[finding('focus-'+str(n))])
    before=engine.snapshot()
    rejects(lambda:engine.control('skip',epoch),'focus_limit')
    assert engine.snapshot()==before


if __name__=='__main__':
    tests=[(name,fn) for name,fn in globals().copy().items() if name.startswith('t_') and callable(fn)]
    for name,fn in tests:
        fn();print('PASS',name)
    print('PASS',len(tests),'focus scenarios')

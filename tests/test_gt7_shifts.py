"""Independent power references, observed phases and explicit offline cache lifecycle."""
import copy, json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src', 'scripts'))
import gt7_shifts as shifts

def curve():
    # Independent synthetic shape, not upstream data.
    return [{'rpm': 1000, 'power': 20, 'torque': 60},
            {'rpm': 4000, 'power': 100, 'torque': 100},
            {'rpm': 6000, 'power': 60, 'torque': 40}]

def t_piecewise_power_equality_uses_both_interpolation_breakpoint_sets():
    result=shifts.calculate(curve(),[2,1])
    target=result[0]
    # 180-.02r = -6.6666667+.0133333333r
    assert abs(target['rpm']-5600)<1e-6
    assert target['status']=='crossing' and abs(target['power_difference'])<1e-8
    assert shifts.interpolate(curve(),999) is None
    assert shifts.interpolate(curve(),6001) is None

def t_curve_endpoint_is_a_partial_reference_not_a_confirmed_rev_limit():
    points=[{'rpm':1000,'power':10,'torque':100},{'rpm':6000,'power':100,'torque':30}]
    target=shifts.calculate(points,[2,1])[0]
    assert target['rpm']==6000 and target['status']=='range-capped'
    assert 'rev_limit_rpm' not in target
    assert shifts.calculate(points,[20,1])[0]['rpm'] is None

def t_malformed_curves_and_ratios_cannot_fabricate_targets():
    for points in ([curve()[1],curve()[0]],curve()+[curve()[-1]],
                   [dict(curve()[0],power=float('nan')),curve()[1]],
                   [dict(curve()[0],power=999),curve()[1]]):
        try:shifts.validate_curve(points)
        except ValueError:pass # expected malformed data
        else:raise AssertionError('invalid source curve must be rejected')
    for ratios in ([1,2],[1,0],[1,float('nan')],[True,1]):
        try:shifts.calculate(curve(),ratios)
        except ValueError:pass # expected malformed gearbox
        else:raise AssertionError('invalid ratios must be rejected')

def rows():
    return [{'t':i*.02,'rpm':rpm,'gear':gear,'throttle_pct':throttle,
             'clutch_engagement':clutch} for i,(rpm,gear,throttle,clutch) in enumerate([
        (5500,2,100,1),(5580,2,100,1),(5660,2,100,1),(5600,2,60,1),
        (5300,2,20,1),(5100,3,0,0),(4400,3,100,.5),(4350,3,100,1),
        (4380,3,100,1),(4410,3,100,1)])]

def t_shift_detector_brackets_pre_cut_and_engaged_post_phase():
    event=shifts.detect_shifts(rows())[0]
    assert event['from_gear']==2 and event['to_gear']==3
    assert event['pre_cut_rpm']==5660 and event['last_old_gear_rpm']==5300
    assert event['post_rpm']==4350 and event['phase_status']=='measured'
    assert event['pre_cut_t_s']<event['gear_change_t_s']<event['post_t_s']

def t_missing_or_ambiguous_phase_is_explicit_instead_of_last_packet_fallback():
    data=rows();data[2]['t']=.09
    event=shifts.detect_shifts(data)[0]
    assert event['pre_cut_rpm'] is None and event['phase_status']!='measured'
    data=rows()
    for row in data:row['clutch_engagement']=None
    event=shifts.detect_shifts(data)[0]
    assert event['pre_cut_rpm'] is None and event['post_rpm'] is None

def t_manual_event_reference_remains_separate_from_source_updates_and_economy_targets():
    manual={'car_id':485,'targets':{'2':5600},'configuration':'Fixed event BoP',
            'provenance':'Own confirmed event test','confirmed':True}
    lap={'car_id':485,'session':1,'lap':8,'start_t_s':10,'strategy':{'targets':{'2':5400}},
         'memberships':[{'start_t_s':10,'end_t_s':20,'stint_id':'medium',
                              'compound':'RM','confirmed':True,'strategy':{'targets':{'2':5400}}}]}
    external={'car_id':485,'curve':curve(),'ratios':[3,2,1],
              'source':{'identity':'Test source','commit':'a'*40,'fetched_at':'test'}}
    result=shifts.analyse(rows(),lap,external,manual,True)
    assert result['reference']['kind']=='manual-event' and result['reference']['event_confirmed']
    event=result['shifts'][0]
    assert event['acceleration_target_rpm']==5600 and event['economy_target_rpm']==5400
    assert event['acceleration_deviation_rpm']==60 and event['economy_deviation_rpm']==260
    assert 'error' not in event
    assert not shifts.analyse(rows(),lap,external,manual,False)['reference']['event_confirmed']
    foreign=copy.deepcopy(manual);foreign['car_id']=486
    assert shifts.analyse(rows(),lap,external,foreign,True)['reference']['kind']=='external'
    assert not shifts.analyse(rows(),lap,external,None,True)['reference']['event_confirmed']

def t_explicit_source_update_is_pinned_bounded_atomic_and_offline_afterwards():
    import io
    import http_util
    with tempfile.TemporaryDirectory() as root:
        cache=shifts.Cache(root)
        calls=[]
        original=http_util.open_url
        def fetch(url, **kwargs):
            calls.append(url)
            if url.endswith('/commits/main'): return io.BytesIO(json.dumps({'sha':'a'*40}).encode())
            assert '/'+('a'*40)+'/' in url, 'all source components must share a pinned revision'
            if url.endswith('485.tsv'): return io.BytesIO(b'rpm\tpower\ttorque\n1000\t20\t60\n4000\t100\t100\n6000\t60\t40\n')
            if url.endswith('stock_ratios.json'):return io.BytesIO(b'{"485":{"ratios":[2,1]}}')
            if url.endswith('revbar.json'):return io.BytesIO(b'{"485":5800}')
            raise AssertionError(url)
        try:
            http_util.open_url=fetch
            saved=cache.update(485)
            assert saved['source']['commit']=='a'*40 and saved['revbar_rpm']==5800
            assert saved['source']['hashes']['curve'] and saved['source']['fetched_at']
            count=len(calls)
            http_util.open_url=lambda *a,**k: (_ for _ in ()).throw(AssertionError('ordinary lookup cannot access the network'))
            assert cache.read(485)==saved and cache.read(486) is None
            assert len(calls)==count
            try:cache.update(485)
            except AssertionError:pass # failed update leaves previous cache intact
            assert cache.read(485)==saved
            for invalid in ('../485',True,-1):
                try:cache.read(invalid)
                except ValueError:pass # path/type guard
                else:raise AssertionError('invalid car IDs cannot select cache paths')
        finally:http_util.open_url=original


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('t_') and callable(fn):
            fn(); print('ok', name)
    print('ALL PASS')

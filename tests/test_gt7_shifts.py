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
    data=rows()
    for row in data[7:]:row['t']+=.2
    event=shifts.detect_shifts(data)[0]
    assert event['post_rpm'] is None and event['phase_status']!='measured', \
        'a capture gap cannot confirm the engaged post-shift phase'


def t_neutral_bridge_remains_an_explicit_ambiguous_upshift():
    data=rows()
    data[5]['gear']=0
    events=shifts.detect_shifts(data)
    assert len(events)==1 and events[0]['from_gear']==2 and events[0]['to_gear']==3, \
        'neutral transitions must retain the observed upshift with unknown phase'
    assert events[0]['pre_cut_rpm'] is None and events[0]['phase_status']!='measured'

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
    assert result['reference'].get('curve') == curve() and not result['reference'].get('curve_event_confirmed', True), \
        'manual targets must not confirm a separately cached external power shape'
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


def t_recorded_reader_keeps_original_phase_packets_and_decoded_ratios():
    import struct
    import test_gt7_laps as fixture
    import gt7_recording as recording
    with tempfile.TemporaryDirectory() as root:
        path = fixture.write_circle_recording(root, n=1200)
        offsets = []
        capture = recording.Recording(path)
        for _ts, _kind, packet in capture.packets():
            if struct.unpack_from('<h', packet, 0x74)[0] == 3:
                offsets.append(capture.pos-len(packet))
        with open(path, 'r+b') as stream:
            for i, offset in enumerate(offsets):
                stream.seek(offset+0x104)
                stream.write(struct.pack('<6f', 3, 2, 1.5, 1.2, 1, .8))
                stream.seek(offset+0xF8)
                stream.write(struct.pack('<f', 1))
                if 100 <= i < 110:
                    phase = rows()[i-100]
                    stream.seek(offset+0x3C);stream.write(struct.pack('<f', phase['rpm']))
                    stream.seek(offset+0x90);stream.write(bytes([phase['gear'], round(phase['throttle_pct']*255/100)]))
                    stream.seek(offset+0xF8);stream.write(struct.pack('<f', phase['clutch_engagement']))
        index = fixture._index(path)
        lap = fixture._lap(index, 3)
        observed = shifts.recorded_rows(path, index, lap)
        assert abs(observed['observed_ratios'][1]-2) < 1e-6
        events = shifts.detect_shifts(observed['rows'])
        assert any(e['pre_cut_rpm'] == 5660 and e['last_old_gear_rpm'] == 5300 for e in events), \
            'reader must preserve the original pre-cut phase and decoded gearbox'
        assert len(observed['rows']) > 1000 and all(b['t'] > a['t'] for a, b in zip(observed['rows'], observed['rows'][1:], strict=False)), \
            'raw phase packets stay ordered and distinct'


def t_context_and_templates_validate_and_retain_event_reference_provenance():
    import gt7_context as context
    manual = {'car_id': 485, 'targets': {'2': 5600}, 'configuration': 'Fixed BoP',
              'provenance': 'Own test', 'confirmed': True}
    data = {'notes': [], 'sessions': {'1': {'settings': {}, 'confirmed': True, 'shift_reference': manual}}}
    invalid = copy.deepcopy(data)
    invalid['sessions']['1']['shift_reference']['targets']['2'] = -1
    try:
        context.validate_data(invalid)
    except ValueError:
        pass  # invalid manual table cannot become saved event context
    else:
        raise AssertionError('context must validate manual shift targets')
    with tempfile.TemporaryDirectory() as root:
        doc = context.save_template(root, 'event', 'Own event', {}, shift_reference=manual)
        copied = context.apply_template(data, doc['templates']['event'], 2)
        target = copied['sessions']['2']
        assert target['shift_reference'] == manual and not target['confirmed'], \
            'template reference provenance survives but new event applicability needs confirmation'
        again = context.copy_session(data, 1, 3)['sessions']['3']
        assert again['shift_reference'] == manual and not again['confirmed']


def t_source_payloads_and_packet_observations_are_independently_bounded():
    import io
    import http_util
    original = http_util.open_url
    with tempfile.TemporaryDirectory() as root:
        try:
            http_util.open_url = lambda *a, **k: io.BytesIO(b'x'*(shifts.MAX_DOWNLOAD+1))
            try:
                shifts.Cache(root).update(485)
            except ValueError as exc:
                assert 'download bound' in str(exc)
            else:
                raise AssertionError('oversized source downloads must be refused')
        finally:
            http_util.open_url = original
        try:
            shifts.detect_shifts([{}]*(shifts.MAX_ROWS+1))
        except ValueError as exc:
            assert 'packet bound' in str(exc)
        else:
            raise AssertionError('oversized phase rows must be refused')


def t_confirmed_event_tables_require_configuration_and_provenance():
    valid = {'car_id': 485, 'targets': {'2': 5500}, 'confirmed': True,
             'configuration': 'Fixed event BoP', 'provenance': 'Own event test'}
    for field in ('configuration', 'provenance'):
        value = dict(valid, **{field: ''})
        try:
            shifts.manual_reference(value)
        except ValueError:
            pass  # applicability needs explicit intended configuration and source
        else:
            raise AssertionError('confirmed event table must name configuration and provenance')


def t_export_freezes_shift_reference_values_and_phase_observations():
    import csv
    import test_gt7_laps as fixture
    import gt7_recording as recording
    with tempfile.TemporaryDirectory() as root:
        path = fixture.write_circle_recording(root)
        index = fixture._index(path)
        manual = {'kind': 'manual-event', 'car_id': fixture.CAR, 'event_confirmed': True,
                  'targets': [{'gear': 2, 'rpm': 5500}], 'configuration': 'Own event', 'provenance': 'Own test'}
        index['shift_reference_snapshots'] = {'ref': manual}
        index['context_snapshot'] = {'revision': 42, 'data': {'notes': [], 'sessions': {}}}
        index['laps'][0]['shift_analysis'] = {'reference_id': 'ref', 'reference': manual, 'shifts': [
            {'from_gear': 2, 'to_gear': 3, 'pre_cut_rpm': 5600, 'post_rpm': 4350,
             'acceleration_deviation_rpm': 100, 'economy_target_rpm': 5300}]}
        output = os.path.join(root, 'export')
        recording.export_csv(path, output, shift_analysis=index)
        with open(os.path.join(output, 'shift-references.json'), encoding='utf-8') as f:
            saved = json.load(f)
        with open(os.path.join(output, 'shifts.csv'), newline='', encoding='utf-8') as f:
            events = list(csv.DictReader(f))
        assert saved['references']['ref'] == manual and events[0]['pre_cut_rpm'] == '5600'
        with open(os.path.join(output, 'context.json'), encoding='utf-8') as f:
            assert json.load(f) == index['context_snapshot'], \
                'export context must be the same snapshot as its shift reference analysis'
        manual['targets'][0]['rpm'] = 6000
        with open(os.path.join(output, 'shift-references.json'), encoding='utf-8') as f:
            assert json.load(f) == saved, 'later reference edits cannot rewrite exported values'


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('t_') and callable(fn):
            fn(); print('ok', name)
    print('ALL PASS')

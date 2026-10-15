#!/usr/bin/env python3
"""Driver review channels, explicit comparisons, localization and portable charts."""
import copy
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import ai_package as packages
import ai_reports as reports
import test_ai_package as fixture
import test_ai_reports as report_fixture


def t_driver_schema_and_channel_preservation():
    with tempfile.TemporaryDirectory() as directory:
        row = fixture.row(2)
        for i, point in enumerate(row['trace']):
            point['steer_deg'] = i - 3.5
        source = fixture.source(Path(directory)/'telemetry-recordings', [row])
        package = packages.build(source, 1, language='de')
        assert package['report_schema_version'] == 2
        assert package['laps'][0]['trace'][0]['steer_deg'] == -3.5
        valid = report_fixture.result(package)
        assert reports.validate(valid, package)['version'] == 2
        for change in ('action', 'observation', 'check'):
            bad = copy.deepcopy(valid); del bad['findings'][0][change]
            report_fixture.fail(lambda bad=bad: reports.validate(bad, package))
        bad = copy.deepcopy(valid); bad['findings'][0]['location_m'] = None
        report_fixture.fail(lambda: reports.validate(bad, package))
        bad = copy.deepcopy(valid); bad['findings'][0]['topic'] = 'data-quality'
        report_fixture.fail(lambda bad=bad: reports.validate(bad, package))
        valid['findings'] = []; valid['exercises'] = []
        assert reports.validate(valid, package)['findings'] == []
        report_fixture.fail(lambda: reports.validate(report_fixture.result(dict(package, report_schema_version=1)), package))
        try: packages.build(source, 1, language='fr')
        except packages.PackageError as error: assert error.code == 'invalid_selection'
        else: raise AssertionError('Unsupported report language accepted')


def t_measured_channels_and_pair_boundaries():
    import ai_driver_review as driver
    points = [dict(d=i*10, speed_kmh=100-abs(2-i)*-10, throttle=100 if i>=3 else 0,
                   brake=70 if i in (1,2) else 0, steer_deg=i*2) for i in range(5)]
    corner = dict(id='T1', number=1, entry_m=0, exit_m=40)
    row = driver.channels(points,[corner])[0]
    assert row['brake_first_m'] == 10 and row['brake_last_m'] == 20
    assert row['peak_abs_steering_deg'] == 8
    invalid=copy.deepcopy(points);invalid[1]['steer_deg']=float('nan')
    assert driver.channels(invalid,[corner])[0]['peak_abs_steering_deg'] is None
    incomplete = copy.deepcopy(points); incomplete[0]['brake'] = 70; incomplete[1].pop('steer_deg')
    incomplete[2].pop('throttle')
    changed = driver.channels(incomplete,[corner])[0]
    assert changed['brake_first_m'] is None
    assert changed['mean_abs_steering_deg'] is None and changed['mean_throttle_pct'] is None
    assert changed['full_throttle_after_min_m'] is None
    a = dict(id='a', recording_id='r', session=1, car_id=1, track_id='x', context=dict(comparison_eligible=True, compound='RM', stint_id='1'))
    b = copy.deepcopy(a); b['id']='b'
    package = dict(comparisons=[dict(lap_id='a',reference_id='b')])
    assert driver.pair_allowed(package,a,b)
    b['context']['stint_id']='2'; assert not driver.pair_allowed(package,a,b)
    b['context']['stint_id']='1'; b['context']['strategy']={'goal':'fuel-save'}
    a['context']['strategy']={'goal':'pace'}; assert not driver.pair_allowed(package,a,b)
    a['context'].pop('strategy'); b['context'].pop('strategy')
    assert not driver.pair_allowed(dict(comparisons=[]),a,b)


def t_references_stay_within_the_known_stint():
    import gt7_context
    with tempfile.TemporaryDirectory() as directory:
        source=fixture.source(Path(directory)/'telemetry-recordings',[fixture.row(2,time=104),fixture.row(3,time=103),fixture.row(5,time=100)])
        store=gt7_context.Store.for_recording(source.path)
        context=store.read(); data=context['data']
        data['sessions']['1']['stints'].append(dict(id='second',start_lap=4,compound='RM',confirmed=True,warmup_laps=0))
        store.save(data,expected=context['revision'])
        source=packages.load_source(source.root,source.name,source.index)
        package=packages.build(source,1)
        pairs=[(c['lap_id'].split('/')[-1],c['reference_id'].split('/')[-1]) for c in package['comparisons']]
        assert ('2','3') in pairs and ('2','5') not in pairs, pairs


def t_explicit_reference_and_legacy_validation():
    with tempfile.TemporaryDirectory() as directory:
        rows = [fixture.row(n,time=100+n) for n in (3,2,4)]
        source=fixture.source(Path(directory)/'telemetry-recordings',rows)
        package=packages.build(source,1)
        valid=report_fixture.result(package)
        target=package['laps'][2]
        valid['findings'][0]['evidence'].append(dict(fact_id=target['id']+'/time_s',value=target['metrics']['time_s']))
        report_fixture.fail(lambda: reports.validate(valid,package))
        bad=report_fixture.result(package);bad['findings'][0]['reference_id']=target['id']
        report_fixture.fail(lambda: reports.validate(bad,package))
        legacy=dict(package,report_schema_version=1)
        assert reports.validate(report_fixture.result(legacy),legacy)['version']==1


def t_charts_and_report_localization():
    import ai_report_charts as charts
    with tempfile.TemporaryDirectory() as directory:
        source=fixture.source(Path(directory)/'telemetry-recordings')
        for language, expected, forbidden in [('de','Lenkwinkel','Validated package facts'),('en','Steering angle','Datenherkunft')]:
            package=packages.build(source,1,language=language)
            report=reports.validate(report_fixture.result(package),package)
            run=dict(report=report,profile='profile')
            document=reports.render(run,package,'html')
            assert '<html lang="'+language+'"' in document
            assert expected in document and forbidden not in document
            assert '<svg' in document and '<details' in document and 'id="charts"' in document
            assert '<script>alert(1)</script>' not in document and '&lt;script&gt;' in document
            assert 'https://example.invalid/track' in document
            assert 'style-src' in document and 'default-src' in document
        hostile=copy.deepcopy(package)
        hostile['laps'][0]['context']['stint_id']='<img src=x onerror=alert(1)>'
        trend=charts.trend(hostile)
        assert '<img' not in trend and '&lt;img' in trend
        unsafe = report_fixture.result(package)
        unsafe['summary'] = '![tracking](https://example.invalid/pixel)\n<script>alert(1)</script>'
        checked = reports.validate(unsafe, package)
        markdown = reports.render(dict(report=checked,profile='profile'), package, 'markdown')
        assert '![tracking](' not in markdown and '<script>' not in markdown
        assert '\\!\\[tracking' in markdown and '\\<script' in markdown
        assert charts._path([dict(d=0,v=1),dict(d=1,v=None),dict(d=2,v=3)],lambda x:x,lambda y:y,'v').count('M')==2
        points=[dict(d=i*5,t=i*.1,v=1) for i in range(10)] + [dict(d=1000,t=20,v=2),dict(d=1005,t=20.1,v=2)]
        assert charts._path(points,lambda x:x,lambda y:y,'v').count('M')==2
        missing=charts.trace_chart('Steering angle',[('lap',source.index['laps'][0]['trace'],'steer_deg','#22d3ee',False)],0,1000,'deg')
        assert 'Not captured' in missing and '<path' not in missing


if __name__ == '__main__':
    for name, test in list(globals().items()):
        if name.startswith('t_'):
            test(); print('PASS '+name)

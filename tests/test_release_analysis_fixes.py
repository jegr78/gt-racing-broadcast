#!/usr/bin/env python3
"""Release review: equal lap times, recovered context and retained sector statistics."""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'scripts'))

import gt7_context as context
import gt7_laps
import gt7_recording as recording
import gt7_telemetry as telemetry
import race_engineer_api
import race_engineer_live as live
from test_gt7_laps import FakeCars, FakeTracks, _index, _pkt, write_circle_recording


def feed(store, path):
    for wall, _kind, plain in recording.Recording(path).packets():
        assert store.put(telemetry.parse_packet(plain), wall)
    assert store.flush(10)


def equal_recording(directory):
    path = write_circle_recording(directory, lap_secs=(20.,) * 4, n=100)
    # Keep observing the next lap beyond the delayed last-time publication window.
    with open(path, 'ab') as capture:
        for k in range(10, 26):
            capture.write(recording._encode(1_700_000_080. + k * .2, ord('~'),
                                            _pkt(5, 50., 2 * math.pi * k / 100, 20000)))
    return path


def t_equal_official_times_survive_archive_live_and_hud():
    with tempfile.TemporaryDirectory() as directory:
        path = equal_recording(directory)
        index = _index(path)
        assert [r['gt7_time_s'] for r in index['laps'][1:]] == [20., 20., 20.]
        assert all(r['trace'][-1]['t'] == 20. for r in index['laps'][1:])
        with open(gt7_laps.cache_path(path), encoding='utf-8') as cache:
            old = json.load(cache)
        old['version'] = 10
        for row in old['laps'][2:]:
            row['gt7_time_s'] = None
        with open(gt7_laps.cache_path(path), 'w', encoding='utf-8') as cache:
            json.dump(old, cache)
        assert [r['gt7_time_s'] for r in _index(path)['laps'][1:]] == [20., 20., 20.]
        engine = telemetry.TelemetryEngine(log_laps=False)
        for wall, _kind, plain in recording.Recording(path).packets():
            engine.update(telemetry.parse_packet(plain), wall)
        assert engine.snapshot()['best_s'] == 20.
        assert abs(engine.snapshot()['avg_lap_s'] - 20.) < 1e-9
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(),
                        queue_max=4096) as store:
            feed(store, path)
            timed = [r for r in store.snapshot()['laps'] if r['lap'] >= 2]
            assert [(r['state'], r['time_s']) for r in timed] == [('confirmed', 20.)] * 3


def t_equal_time_waits_for_publication_and_rejects_stale_or_other_laps():
    with tempfile.TemporaryDirectory() as directory:
        replay = gt7_laps._Replay()
        replay.run(recording.Recording(equal_recording(directory)))
        raw = replay.laps[2]
        edge = raw['start'] + 20.
        packet = telemetry.parse_packet(_pkt(4, 50., 0, 20000))
        matcher = recording.LapTimeMatcher()
        matcher.update(packet._replace(lap=3), edge - .2)
        matcher.lap_closed(raw, edge)
        matcher.update(packet, edge + .2)
        assert raw['gt7_time_s'] is None, 'unchanged previous time was accepted at the line'
        assert matcher.waiting(raw, edge + recording.GT7_TIME_WINDOW_S + .1), \
            'an idle tick must leave time for the next delayed packet'
        matcher.update(packet, edge + recording.GT7_TIME_WINDOW_S)
        assert raw['gt7_time_s'] == 20.
        for change in ({'elapsed': 10., 'start': edge - 10.},
                       {'capture_complete': False}, {'data_quality': 'interrupted'}):
            candidate = dict(raw, **change)
            matcher = recording.LapTimeMatcher()
            matcher.update(packet._replace(lap=3), edge - .2)
            matcher.lap_closed(candidate, edge)
            matcher.update(packet, edge + recording.GT7_TIME_WINDOW_S)
            assert candidate['gt7_time_s'] is None, change
        for changed in (packet._replace(lap=5), packet._replace(car_id=1),
                        packet._replace(paused=True), packet._replace(loading=True),
                        packet._replace(on_track=False)):
            matcher = recording.LapTimeMatcher()
            matcher.update(packet._replace(lap=3), edge - .2)
            matcher.lap_closed(raw, edge)
            matcher.update(changed, edge + recording.GT7_TIME_WINDOW_S)
            assert raw['gt7_time_s'] is None
        matcher = recording.LapTimeMatcher()
        matcher.update(packet._replace(lap=3), edge - .2)
        matcher.lap_closed(raw, edge)
        matcher.update(packet._replace(last_ms=20100), edge + .2)
        assert raw['gt7_time_s'] == 20.1, 'a delayed different value must win before tie settlement'
        for last_ms in (20000, 20100):
            matcher = recording.LapTimeMatcher()
            matcher.update(packet._replace(lap=3), edge - .2)
            matcher.lap_closed(raw, edge)
            matcher.update(packet._replace(last_ms=last_ms), edge + 6.)
            assert raw['gt7_time_s'] is None, 'late stale timing was accepted'


def t_recovered_stint_boundaries_match_archive_after_summary_eviction():
    with tempfile.TemporaryDirectory() as directory:
        path = write_circle_recording(directory, lap_secs=tuple(20. + i * .1 for i in range(8)))
        index = _index(path)
        data = {'notes': [], 'sessions': {'1': {'confirmed': True, 'stints': [
            {'id': 'initial', 'start_lap': 2, 'start_recording_s': 20.,
             'compound': 'RM', 'confirmed': True, 'tyre_service': True, 'warmup_laps': 5}]}}}
        context.Store.for_recording(path).save(data, 0)
        lookup = {(r['session'], r['lap']): r for r in index['laps']}
        expected = {r['lap']: context.annotate(data, r, lap_lookup=lookup) for r in index['laps']}
        source = context.source_identity(path)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars()) as store:
            api = race_engineer_api.Service(store)
            old_limit = live.SUMMARIES_MAX
            live.SUMMARIES_MAX = 2
            try:
                assert api.reference(source, path)['state'] == 'pending'
                assert store.wait_recovery(10)
                result = api.reference(source, path)
                assert result['state'] == 'ready'
                assert len(result['laps']) == 2
                for row in result['laps']:
                    original = expected[row['lap']]
                    assert row['analysis_role'] == original['analysis_role']
                    assert row['comparison_eligible'] == original['comparison_eligible']
                assert result['laps'][0]['lap'] == 7
                assert result['laps'][0]['analysis_role'] == 'regular'
            finally:
                live.SUMMARIES_MAX = old_limit
                api.close()


def t_theory_keeps_early_sectors_after_default_detail_eviction():
    with tempfile.TemporaryDirectory() as directory:
        times = (20., 16.) + tuple(20. + i * .1 for i in range(18))
        path = write_circle_recording(directory, lap_secs=times, n=200)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(),
                        queue_max=8192) as store:
            feed(store, path)
            snap = store.snapshot()
            best = next(r for r in snap['laps'] if r['lap'] == 2)
            # The best lap keeps its detail; the other early laps are evicted.
            assert store.detail(best['id'])['state'] == 'ready'
            early = next(r for r in snap['laps'] if r['lap'] == 3)
            assert store.detail(early['id'])['state'] == 'evicted'
            assert store.stats()['details'] <= live.DETAILS_MAX
            assert snap['best_s'] == 16.
            assert snap['theoretical_s'] == 16.
            api = race_engineer_api.Service(store)
            try:
                assert api.snapshot()['theoretical_s'] == 16.
                public = api.history(None)['laps']
                assert all('sectors' not in row and 'trace' not in row for row in public)
            finally:
                api.close()


def t_theory_composes_evicted_and_retained_sectors_instead_of_only_clamping():
    with tempfile.TemporaryDirectory() as directory:
        writer = recording.RecordingWriter(directory, 'Solo', 'dev', queue_max=0)
        laps = ([4.] * 5, [5., 2., 3., 3., 3.], [2., 4.5, 4.5, 4.5, 4.5], [4.2] * 5)
        wall, last_ms = 1_700_000_000., -1
        for li, sectors in enumerate(laps):
            for sector, duration in enumerate(sectors):
                for step in range(20):
                    k = sector * 20 + step
                    if li >= 2 and k == 5:
                        last_ms = round(sum(laps[li - 1]) * 1000)
                    writer.put(wall, '~', _pkt(li + 1, 200. / duration,
                                               2 * math.pi * k / 100, last_ms))
                    wall += duration / 20
        for k in range(26):
            writer.put(wall + k * .21, '~', _pkt(5, 1000. / 21.,
                                                2 * math.pi * k / 100, 21000))
        writer.close()
        index = _index(writer.path)
        expected = gt7_laps.theoretical_best([r for r in index['laps'] if r['pace_eligible']])
        assert expected < 15., 'fixture must combine sectors from different laps'
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(),
                        queue_max=4096, details_max=1) as store:
            feed(store, writer.path)
            snap = store.snapshot()
            # Lap 2 is the best lap and keeps its detail; lap 3's fast first sector is evicted.
            assert store.detail(next(r['id'] for r in snap['laps'] if r['lap'] == 3))['state'] == 'evicted'
            assert snap['theoretical_s'] == expected
            assert snap['theoretical_s'] < snap['best_s']


if __name__ == '__main__':
    for name, function in sorted(list(globals().items())):
        if name.startswith('t_'):
            function()
            print('PASS', name)

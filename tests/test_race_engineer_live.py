#!/usr/bin/env python3
"""Shared live preparation uses the exact offline packets and numerical helpers."""
import concurrent.futures
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'src', 'scripts'))
sys.path.insert(0, HERE)
import gt7_recording as gr
import gt7_telemetry as tm
import race_engineer_live as live
from test_gt7_laps import FakeCars, FakeTracks, _index, write_circle_recording


def feed(store, path):
    for wall, _kind, plain in gr.Recording(path).packets():
        store.put(tm.parse_packet(plain), wall)
    assert store.flush(10), 'worker did not drain accepted input'


def t_confirmed_matches_offline_and_provisional_stays_observed():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), queue_max=4096) as store:
            feed(store, path)
            snap = store.snapshot()
            offline = _index(path)
            confirmed = [r for r in snap['laps'] if r['state'] == 'confirmed']
            assert [r['lap'] for r in confirmed] == [2, 3, 4]
            for row in confirmed:
                detail = store.detail(row['id'])['lap']
                expected = next(r for r in offline['laps'] if r['lap'] == row['lap'])
                assert detail['gt7_time_s'] == expected['gt7_time_s']
                assert detail['trace'] == expected['trace']
                assert detail['sectors'] == expected['sectors']
            current = snap['current']
            assert current['state'] == 'provisional' and current['time_s'] is None
            assert not current['pace_eligible']
            assert current['trace'][-1]['d'] <= current['distance_m']
            assert snap['best_s'] == 16.0


def t_stale_pause_generation_and_collection_switch():
    from test_gt7_laps import _pkt
    clock = [10.0]
    enabled = [True]
    with live.Store(lambda: enabled[0], clock=lambda: clock[0]) as store:
        p = tm.parse_packet(_pkt(1, 30, 0))
        store.put(p, 100.0)
        assert store.flush()
        a = store.snapshot()
        assert a['receipt']['state'] == 'live' and a['receipt']['age_s'] == 0
        clock[0] = 15.0
        b = store.snapshot()
        assert b['receipt']['state'] == 'stale' and b['instruments'] == a['instruments']
        store.put(p._replace(paused=True), 101)
        assert store.flush()
        assert store.snapshot()['receipt']['state'] == 'paused'
        store.put(p._replace(on_track=False, paused=False), 102)
        assert store.flush()
        assert store.snapshot()['receipt']['state'] == 'menu'
        enabled[0] = False
        store.refresh_enabled()
        assert not store.put(p, 103)
        assert store.snapshot()['current'] is None
        enabled[0] = True
        store.refresh_enabled()
        store.put(p, 104)
        assert store.flush()
        assert store.snapshot()['session_id'] != a['session_id']
        with live.Store(lambda: True) as restart:
            assert restart.generation != store.generation


def t_bounds_eviction_concurrent_readers_and_async_recovery():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d, lap_secs=tuple(20.0 - i * .1 for i in range(12)), n=100)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), details_max=2,
                        summaries_max=8, queue_max=4096, runtime_base=d) as store:
            feed(store, path)
            snap = store.snapshot()
            assert len(snap['laps']) == 8 and store.stats()['details'] <= 2
            old = snap['laps'][0]
            assert store.detail(old['id'])['state'] == 'evicted'
            store.bind_recording(path)
            pending = store.detail(old['id'])
            assert pending['state'] in ('pending', 'ready')
            assert store.wait_recovery(10)
            recovered = store.detail(old['id'])
            assert recovered['state'] == 'ready' and recovered['lap']['id'] == old['id']
            assert store.snapshot()['current']['id'] == snap['current']['id']
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                reads = list(pool.map(lambda _: store.snapshot(), range(40)))
            assert all(r['revision'] == reads[0]['revision'] for r in reads)
            assert store.stats()['replays'] == 1
            recovered['lap']['trace'].clear()
            assert store.detail(old['id'])['lap']['trace'], 'reader mutated shared history'
            os.unlink(path)
            assert store.detail(old['id'])['state'] == 'unavailable'


def t_ingestion_does_not_wait_for_analysis_and_overflow_is_visible():
    from test_gt7_laps import _pkt
    block, entered = threading.Event(), threading.Event()
    with live.Store(lambda: True, queue_max=2) as store:
        original = store._consume
        def slow(item):
            entered.set()
            assert block.wait(5)
            original(item)
        store._consume = slow
        p = tm.parse_packet(_pkt(1, 30, 0))
        store.put(p, 100)
        assert entered.wait(2)
        start = time.monotonic()
        accepted = [store.put(p, 101 + i) for i in range(10)]
        elapsed = time.monotonic() - start
        pending = store.snapshot()
        block.set()
        assert elapsed < .1 and not all(accepted), 'ingestion waited for preparation'
        assert pending['receipt']['state'] == 'preparing'
        assert pending['receipt']['packet_age_s'] < .1
        assert store.flush()
        assert store.stats()['dropped'] > 0
        assert not store.snapshot()['current']['pace_eligible']


def t_context_revision_and_session_limits():
    import gt7_context
    from test_gt7_laps import _pkt
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), queue_max=4096) as store:
            store.bind_recording(path)
            feed(store, path)
            old = store.snapshot()
            context = gt7_context.Store.for_recording(path)
            doc = context.read()
            data = {'notes': [], 'sessions': {'1': {'settings': {'bop': True},
                    'confirmed': True, 'stints': [], 'lap_roles': {'3': 'pit'}}}}
            context.save(data, doc['revision'])
            store._read_context()
            new = store.snapshot()
            assert new['context_revision'] > old['context_revision']
            changed = next(r for r in new['laps'] if r['lap'] == 3)
            assert changed['analysis_role'] == 'pit' and not changed['comparison_eligible']
            assert new['current']['id'] == old['current']['id']
            assert new['best_s'] == 20.0
            with open(context.path, 'w', encoding='utf-8') as f:
                f.write('{invalid')
            store._read_context()
            invalid = store.snapshot()
            assert invalid['context_revision'] is None and invalid['warnings']
            assert not any(r['context_confirmed'] for r in invalid['laps'])
            assert not invalid['current']['context_confirmed']
            os.unlink(context.path)
            context.save(data, 0)
            store._read_context()
            assert store.snapshot()['context_revision'] == 1
            assert store.snapshot()['best_s'] == 20.0
    with live.Store(lambda: True, queue_max=4096) as store:
        p = tm.parse_packet(_pkt(1, 30, 0))
        for i in range(30):
            store.put(p._replace(car_id=100 + i), 100 + i * 5)
        assert store.flush()
        snap = store.snapshot()
        assert len(snap['sessions']) <= live.SESSIONS_MAX
        assert len({r['id'] for r in snap['laps']}) == len(snap['laps'])
        assert len({r['session_id'] for r in snap['laps']}) > 1


def t_unconfirmed_time_never_becomes_a_confirmed_best():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), queue_max=4096) as store:
            for wall, _kind, plain in gr.Recording(path).packets():
                store.put(tm.parse_packet(plain)._replace(last_ms=-1), wall)
            assert store.flush()
            snap = store.snapshot()
            assert snap['best_s'] is None
            assert all(r['state'] == 'unconfirmed' and r['time_s'] is None
                       and not r['pace_eligible'] for r in snap['laps'])


def t_disabled_collection_clears_published_details_and_missing_channels():
    enabled = [True]
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        with live.Store(lambda: enabled[0], queue_max=4096) as store:
            feed(store, path)
            lid = store.snapshot()['laps'][0]['id']
            assert store.detail(lid)['state'] == 'ready'
            enabled[0] = False
            store.refresh_enabled()
            assert store.detail(lid)['state'] == 'unavailable'
    from test_gt7_laps import _pkt
    pkt = tm.parse_packet(_pkt(1, 30, 0))._replace(rpm=None, gear=None, steer_rad=None,
                                               speed_mps=float('nan'))
    values = live.instruments(pkt)
    assert values['rpm'] is None and values['gear'] is None
    assert values['speed_kmh'] is None and values['steer_rad'] is None


def t_recovery_source_replacement_and_deleted_context_fail_closed():
    import gt7_context
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        source = gt7_context.source_identity(path)
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars()) as store:
            store.bind_recording(path)
            store.recording_snapshot(source, path)
            assert store.wait_recovery(10)
            assert store.recording_snapshot(source, path)['state'] == 'ready'
            with open(path, 'rb') as f:
                data = f.read()
            st = os.stat(path)
            with open(path, 'wb') as f:
                f.write(data.replace(source.encode(), ('0' * len(source)).encode(), 1))
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            assert store.recording_snapshot(source, path)['state'] == 'unavailable'
            os.unlink(path)
            store._read_context()
            time.sleep(.25)
            assert store._worker.is_alive(), 'missing context stopped telemetry preparation'


def t_late_recovery_target_is_not_declared_exhausted():
    import gt7_context
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d, lap_secs=(20.0,) * 24, n=100)
        source = gt7_context.source_identity(path)
        original = live._bounded_recording
        reached, release = threading.Event(), threading.Event()
        def held(*args, **kwargs):
            result = original(*args, **kwargs)
            reached.set()
            assert release.wait(5)
            result['requested'] = set(args[4])  # EOF barrier, before the replay seals its targets.
            return result
        live._bounded_recording = held
        try:
            with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars()) as store:
                assert store.recording_detail(source, path, 1, 2)['state'] == 'pending'
                assert reached.wait(5)
                assert store.recording_detail(source, path, 1, 3)['state'] == 'pending'
                release.set()
                assert store.wait_recovery(10)
                assert store.recording_detail(source, path, 1, 2)['state'] == 'ready'
                assert store.recording_detail(source, path, 1, 3)['state'] == 'pending'
                assert store.wait_recovery(10)
                assert store.recording_detail(source, path, 1, 3)['state'] == 'ready'
        finally:
            release.set()
            live._bounded_recording = original


def t_epoch_recovery_and_context_bindings_require_the_actual_source():
    import gt7_context
    from test_gt7_laps import _pkt
    enabled = [True]
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        source = gt7_context.source_identity(path)
        with live.Store(lambda: enabled[0], tracks=FakeTracks(), cars=FakeCars(),
                        queue_max=4096, details_max=2) as store:
            p = tm.parse_packet(_pkt(1, 30, 0))
            for i in range(3):
                store.put(p._replace(car_id=i + 1), 100 + i * 5)
            assert store.flush()
            store.bind_recording(path)
            assert store._session_offset > 0
            enabled[0] = False
            store.refresh_enabled()
            enabled[0] = True
            store.refresh_enabled()
            feed(store, path)
            old = next(r for r in store.snapshot()['laps'] if r['lap'] == 2)
            assert old['recording_session'] >= 1
            assert store.detail(old['id'])['state'] == 'pending'
            assert store.wait_recovery(10)
            assert store.detail(old['id'])['state'] == 'ready'
            with open(path, 'rb') as f:
                raw = f.read()
            with open(path, 'wb') as f:
                f.write(raw.replace(source.encode(), ('1' * len(source)).encode(), 1))
            replacement = gt7_context.Store.for_recording(path)
            replacement.save({'notes': [], 'sessions': {'1': {'confirmed': True,
                'stints': [{'id': 'new', 'start_lap': 1, 'confirmed': True,
                            'compound': 'RH'}]}}}, 0)
            store._read_context()
            snap = store.snapshot()
            assert snap['warnings'] and snap['context_revision'] is None
            assert snap['current']['compound'] is None


def t_telemetry_consumer_is_additive_and_failures_do_not_stop_the_store():
    from test_gt7_laps import _pkt
    class Consumer:
        def __init__(self):
            self.values = []
        def put(self, pkt, wall):
            self.values.append((pkt, wall))
    consumer = Consumer()
    store = tm.TelemetryStore(engineer=consumer)
    p = tm.parse_packet(_pkt(1, 30, 0))
    store.update(p, 100)
    assert consumer.values == [(p, 100)]
    assert store.recorder is None
    class Broken:
        def put(self, *_args):
            raise ValueError('synthetic preparation failure')
    store.engineer = Broken()
    store.update(p, 101)
    assert store.data()['lap'] == 1


def t_relay_collection_follows_the_profile_without_a_browser_or_recording():
    import test_console_gate as fixture
    import race_engineer_access
    from test_gt7_laps import _pkt
    with tempfile.TemporaryDirectory() as d:
        access = race_engineer_access.Store(d, available=True)
        telemetry = tm.TelemetryStore()
        store = fixture.m.engineer_live_for(access, telemetry, os.path.join(d, 'telemetry-recordings'), d, None)
        try:
            pkt = tm.parse_packet(_pkt(1, 30, 0))
            assert not store.put(pkt, 100)
            access.update(enabled=True, expected=0)
            store.refresh_enabled()
            telemetry.update(pkt, 100)
            assert store.flush()
            assert store.snapshot()['current'] is not None
            assert telemetry.recorder is None, 'Engineer opt-in started a recording'
            access.update(enabled=False, expected=1)
            store.refresh_enabled()
            assert not store.put(pkt, 101)
        finally:
            store.close()


def t_active_recording_prefix_survives_appends_and_detects_changed_measurements():
    import struct
    from test_gt7_laps import _pkt
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        part = path + '.part'
        os.rename(path, part)
        original = live._bounded_recording
        reached, release = threading.Event(), threading.Event()
        def held(*args, **kwargs):
            result = original(*args, **kwargs)
            reached.set()
            assert release.wait(5)
            return result
        live._bounded_recording = held
        try:
            with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(),
                            queue_max=4096, details_max=2) as store:
                store.bind_recording(part)
                feed(store, part)
                brief = next(r for r in store.snapshot()['laps'] if r['lap'] == 2)
                assert store.detail(brief['id'])['state'] == 'pending'
                assert reached.wait(5)
                with open(part, 'ab') as f:
                    f.write(gr._encode(1_700_000_080.6, ord('~'), _pkt(5, 50, .2, 20000)))
                release.set()
                assert store.wait_recovery(10)
                assert store.detail(brief['id'])['state'] == 'ready'
                with open(part, 'ab') as f:
                    f.write(gr._encode(1_700_000_080.65, ord('~'), _pkt(5, 50, .21, 20000)))
                assert store.detail(brief['id'])['state'] == 'ready'
                assert store.stats()['replays'] == 1, 'reader replayed after a normal append'
                rec = gr.Recording(part)
                for _wall, _kind, plain in rec.packets():
                    if tm.parse_packet(plain).lap == 2:
                        offset = rec.record_start + gr._REC.size + tm.OFF_SPEED
                        break
                with open(part, 'r+b') as f:
                    f.seek(offset)
                    f.write(struct.pack('<f', 55.0))
                assert store.detail(brief['id'])['state'] == 'pending'
                assert store.wait_recovery(10)
                assert store.detail(brief['id'])['state'] == 'unavailable'
                with open(part, 'r+b') as f:
                    f.truncate(100)
                assert store.detail(brief['id'])['state'] != 'ready'
        finally:
            release.set()
            live._bounded_recording = original


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_'):
            fn()
            print('ok', name)
    print('ALL PASS')

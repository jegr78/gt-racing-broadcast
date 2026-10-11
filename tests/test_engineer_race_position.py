"""Measured race-position parsing, lap-line preservation and public projection."""
import json
import math
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'scripts'))
import gt7_telemetry as tm
import gt7_laps
import gt7_recording
import race_engineer_live as live
import race_engineer_api as api
from test_gt7_laps import _pkt, FakeTracks, FakeCars


def packet(lap=1, position=15, cars=15, angle=0, flags=None):
    raw = bytearray(_pkt(lap, 50, angle))
    struct.pack_into('<hh', raw, 0x84, position, cars)
    if flags is not None:
        struct.pack_into('<H', raw, tm.OFF_FLAGS, flags)
    return bytes(raw)


def t_valid_and_invalid_position_pairs():
    for pos, count in [(15, 15), (1, 2), (2, 2), (1, 1), (32767, 32767)]:
        for length in (0x128, 0x13c, 0x158):
            parsed = tm.parse_packet(packet(position=pos, cars=count)[:length])
            assert (parsed.race_position, parsed.car_count) == (pos, count), 'valid measured race position lost'
    for pos, count in [(-1, -1), (0, 0), (1, 0), (-1, 15), (0, 15), (16, 15), (1, -1)]:
        parsed = tm.parse_packet(packet(position=pos, cars=count))
        assert parsed.race_position is None, 'invalid measured position accepted'
        assert parsed.car_count == (count if count > 0 else None), 'invalid field size accepted'


def t_snapshot_uses_measured_position_only():
    with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars()) as store:
        service = api.Service(store)
        try:
            for offset, pos in enumerate((15, 7, 1)):
                store.put(tm.parse_packet(packet(position=pos)), 100 + offset)
                assert store.flush(10)
                instruments = service.snapshot()['instruments']
                assert (instruments['race_position'], instruments['car_count']) == (pos, 15)
            for flags in (0, tm.FLAG_ON_TRACK | tm.FLAG_LOADING):
                store.put(tm.parse_packet(packet(flags=flags)), 120)
                assert store.flush(10)
                instruments = service.snapshot()['instruments']
                assert instruments['race_position'] is None and instruments['car_count'] is None, 'off-session race position retained'
        finally:
            service.close()


def t_lap_line_position_matches_live_and_offline_index():
    with tempfile.TemporaryDirectory() as root:
        writer = gt7_recording.RecordingWriter(root, 'Solo', 'dev', queue_max=0)
        # Position changes at the boundary. The previous packet's position is wrong.
        for lap in range(1, 4):
            for k in range(80):
                writer.put(100 + (lap-1)*20 + k*.25, '~', packet(lap, position=lap if k == 0 else 15, angle=2*math.pi*k/80))
        writer.close()
        with live.Store(lambda: True, tracks=FakeTracks(), cars=FakeCars(), queue_max=4096) as store:
            for wall, _, raw in gt7_recording.Recording(writer.path).packets():
                store.put(tm.parse_packet(raw), wall)
            assert store.flush(10)
            service = api.Service(store)
            try:
                history = service.history(None)
                lap = next(row for row in history['laps'] if row['lap'] == 2)
                assert (lap['line_position'], lap['line_car_count']) == (3, 15), 'lap line used previous packet position'
                assert service.snapshot()['current'].get('line_position') is None, 'unfinished lap claimed a finish position'
                indexed = gt7_laps.index(writer.path, FakeTracks(), FakeCars(), root)
                row = next(row for row in indexed['laps'] if row['lap'] == 2)
                assert (row['line_position'], row['line_car_count']) == (3, 15), 'offline index lost lap-line position'
                cache = Path(gt7_laps.cache_path(writer.path))
                old = json.loads(cache.read_text()); old['version'] = 7
                for cached in old['laps']:
                    cached.pop('line_position', None); cached.pop('line_car_count', None)
                cache.write_text(json.dumps(old))
                rebuilt = gt7_laps.index(writer.path, FakeTracks(), FakeCars(), root)
                assert next(r for r in rebuilt['laps'] if r['lap'] == 2)['line_position'] == 3, 'legacy index was not rebuilt'
            finally:
                service.close()



def t_menu_boundary_does_not_claim_a_line_position():
    for flags in (0, tm.FLAG_ON_TRACK | tm.FLAG_LOADING):
        engine = tm.TelemetryEngine(); laps = []; engine.on_lap = laps.append
        engine.update(tm.parse_packet(packet()), 100)
        engine.update(tm.parse_packet(packet(lap=2, flags=flags)), 101)
        assert laps[-1]['line_position'] is None, 'menu/loading packet claimed a line position'


def t_missing_line_position_and_old_resume_packet():
    engine = tm.TelemetryEngine()
    laps = []; engine.on_lap = laps.append
    engine.update(tm.parse_packet(packet(lap=1)), 100)
    engine.update(tm.parse_packet(packet(lap=2, position=-1, cars=-1)), 101)
    assert laps[-1]['line_position'] is None and laps[-1]['line_car_count'] is None
    saved = engine.resume_state()
    saved['last'].pop('race_position'); saved['last'].pop('car_count')
    restored = tm.TelemetryEngine(); restored.restore(saved)
    assert restored._last.race_position is None and restored._last.car_count is None
    engine.update(tm.parse_packet(packet(lap=0, flags=0)), 102)
    assert laps[-1]['reason'] == 'session change'
    assert laps[-1]['line_position'] is None, 'abandoned lap claimed a line position'

if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn()
            print('PASS', name)

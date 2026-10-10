#!/usr/bin/env python3
"""Confirmed packet channels, diagnostic uncertainty and bounded raw-detail reads."""
import json
import math
import os
import struct
import sys
import tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src', 'scripts'))
import gt7_channels as c
import gt7_recording as r
import test_gt7_laps as fixture


def t_decoding_keeps_geometry_gearbox_units_and_constant_zero_distinct():
    plain = bytearray(fixture._pkt(2, 0, 0))
    struct.pack_into('<4f', plain, 0x1C, .1, .2, .3, .4)
    struct.pack_into('<4f', plain, 0xA4, 10, 20, 30, 40)
    struct.pack_into('<4f', plain, 0xB4, .31, .32, .33, .34)
    struct.pack_into('<6f', plain, 0x104, 2.508, 1.831, 1.438, 1.183, 1.019, .919)
    struct.pack_into('<f', plain, 0xF8, .7)
    rows = c.decode(plain)
    assert math.isclose(rows['rotation_w']['value'], .4, abs_tol=1e-7)
    assert rows['wheel_fl_rad_s']['value'] == 10
    assert math.isclose(rows['gear_ratio_2']['value'], 1.831, abs_tol=1e-6)
    assert rows['speed_kmh'] == {'value': 0.0, 'state': 'value', 'raw': '00000000'}
    normal = {d['key']: d for d in c.descriptors() if not d['diagnostic']}
    assert normal['rotation_w']['unit'] == '1' and normal['wheel_fl_rad_s']['unit'] == 'rad/s'
    assert 'raw_0x12c' not in normal and 'throttle_input_pct' not in normal
    assert normal['alert_max_rpm']['label'] != 'Rev limit'


def t_missing_nonfinite_and_unset_are_not_silently_zero():
    plain = bytearray(fixture._pkt(2, 30, 0))
    plain[0x90] = 0xF3
    struct.pack_into('<f', plain, 0xF8, float('nan'))
    rows = c.decode(plain)
    assert rows['suggested_gear']['value'] is None and rows['suggested_gear']['state'] == 'unset'
    assert rows['clutch_engagement']['value'] is None and rows['clutch_engagement']['state'] == 'nonfinite'
    base = c.decode(plain[:0x128])
    assert base['steer_deg']['value'] is None and base['steer_deg']['state'] == 'missing'
    assert rows['raw_0x12c']['state'] == 'value'
    json.dumps(rows, allow_nan=False)


def t_raw_time_window_preserves_single_packet_cut_and_uses_sparse_capture_offsets():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td, n=1200)
        # Change exactly one retained raw packet; the distance-resampled default charts
        # cannot guarantee this short interruption survives.
        recording = r.Recording(path)
        offsets = []
        for ts, _kind, plain in recording.packets():
            if struct.unpack_from('<h', plain, 0x74)[0] == 3:
                offsets.append((recording.pos - len(plain), ts))
        offset, ts = offsets[100]
        with open(path, 'r+b') as f:
            f.seek(offset + 0x91)
            f.write(b'\0')
        idx = fixture._index(path)
        lap = fixture._lap(idx, 3)
        result = c.window(path, idx, lap, ['throttle_pct', 'gear'], 'time', 1, 2, fixture.FakeTracks())
        assert any(row['throttle_pct'] == 0 for row in result['rows']), 'a one-packet cut must survive temporal detail'
        assert len(result['rows']) < 100 and result['seek_offset'] > recording.header_end
        assert result['time_basis'] == 'receiver clock, seconds from recorded lap boundary'
        assert result['availability']['gear']['constant']
        for keys, axis, start, end in [(['no_such_channel'], 'time', 0, 1), (['rpm'], 'time', 0, 31), (['rpm'], 'time', 3, 2), (['rpm'], 'bad', 0, 1)]:
            try:
                c.window(path, idx, lap, keys, axis, start, end, fixture.FakeTracks())
            except ValueError:
                pass
            else:
                raise AssertionError('invalid channel/window request must fail with a domain error')


def t_distance_windows_use_common_geometry_and_refuse_unbounded_allocations():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td)
        idx = fixture._index(path)
        lap = fixture._lap(idx, 3)
        result = c.window(path, idx, lap, ['rpm', 'on_track'], 'distance', 100, 200, fixture.FakeTracks())
        assert result['rows'][0]['d'] == 100 and result['rows'][-1]['d'] == 200
        assert result['distance_basis'] == 'reference geometry'
        assert all(row['on_track'] == 1 for row in result['rows'])
        for axis, start, end in [('distance', 0, 1001), ('time', 100, 101)]:
            try:
                c.window(path, idx, lap, ['rpm'], axis, start, end, fixture.FakeTracks())
            except ValueError as exc:
                assert 'recorded lap' in str(exc)
            else:
                raise AssertionError('windows outside the recorded lap must be rejected before allocation')


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('t_') and callable(fn):
            fn(); print('ok', name)
    print('ALL PASS')

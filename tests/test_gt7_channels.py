#!/usr/bin/env python3
"""Confirmed packet channels, diagnostic uncertainty and bounded raw-detail reads."""
import json
import csv
import math
import os
import struct
import sys
import tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src', 'scripts'))
import gt7_channels as c
import gt7_channel_detail as detail
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
    assert all(d['type'] == 'step' for d in c.descriptors(True)), 'diagnostic packet interpretations must not be interpolated'
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
        result = detail.window(path, idx, lap, ['throttle_pct', 'gear'], 'time', 1, 2, fixture.FakeTracks())
        assert any(row['throttle_pct'] == 0 for row in result['rows']), 'a one-packet cut must survive temporal detail'
        assert len(result['rows']) < 100 and result['seek_offset'] > recording.header_end
        assert result['time_basis'] == 'receiver clock, seconds from recorded lap boundary'
        assert result['availability']['gear']['constant']
        for keys, axis, start, end in [(['no_such_channel'], 'time', 0, 1), (['rpm'], 'time', 0, 31), (['rpm'], 'time', 3, 2), (['rpm'], 'bad', 0, 1)]:
            try:
                detail.window(path, idx, lap, keys, axis, start, end, fixture.FakeTracks())
            except ValueError:
                pass  # a refused detail request is the expected domain failure
            else:
                raise AssertionError('invalid channel/window request must fail with a domain error')


def t_distance_windows_use_common_geometry_and_refuse_unbounded_allocations():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td)
        idx = fixture._index(path)
        lap = fixture._lap(idx, 3)
        result = detail.window(path, idx, lap, ['rpm', 'on_track'], 'distance', 100, 200, fixture.FakeTracks())
        assert result['rows'][0]['d'] == 100 and result['rows'][-1]['d'] == 200
        assert result['distance_basis'] == 'reference geometry'
        assert all(row['on_track'] == 1 for row in result['rows'])
        for axis, start, end in [('distance', 0, 1001), ('time', 100, 101)]:
            try:
                detail.window(path, idx, lap, ['rpm'], axis, start, end, fixture.FakeTracks())
            except ValueError as exc:
                assert 'recorded lap' in str(exc)
            else:
                raise AssertionError('windows outside the recorded lap must be rejected before allocation')


def t_csv_preserves_legacy_columns_and_exports_confirmed_states_with_optional_diagnostics():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td, n=100)
        out = os.path.join(td, 'export')
        r.export_csv(path, out, diagnostics=True)
        with open(os.path.join(out, 'samples.csv'), newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        expected = ('t_s', 'session', 'lap', 'lap_t_s', 'lap_dist_m', 'on_track', 'paused', 'speed_kmh',
                    'throttle_pct', 'brake_pct', 'throttle_input_pct', 'brake_input_pct', 'steer_deg',
                    'gear', 'rpm', 'fuel_l', 'tyre_fl_c', 'tyre_fr_c', 'tyre_rl_c', 'tyre_rr_c',
                    'pos_x', 'pos_y', 'pos_z', 'car_id')
        assert tuple(rows[0])[:len(expected)] == expected, 'existing CSV names and order must remain intact'
        assert rows[0]['water_temp_c'] == '0.000000' and rows[0]['water_temp_c__state'] == 'value'
        assert rows[0]['gear_ratio_2'] == '' and rows[0]['gear_ratio_2__state'] == 'unset'
        with open(os.path.join(out, 'channels.json'), encoding='utf-8') as f:
            schema = json.load(f)
        assert schema['version'] == c.SCHEMA_VERSION
        assert {d['key'] for d in schema['channels']} == {d['key'] for d in c.descriptors(False)}
        assert schema['compatibility']['throttle_input_pct']['uncertainty']
        with open(os.path.join(out, 'diagnostics.csv'), newline='', encoding='utf-8') as f:
            raw = list(csv.DictReader(f))
        assert len(raw) == len(rows) and raw[0]['raw_0x12c__raw'] == '0x00000000'
        other = os.path.join(td, 'normal-only')
        r.export_csv(path, other)
        assert not os.path.exists(os.path.join(other, 'diagnostics.csv'))


def t_distance_interpolation_does_not_label_nonfinite_measurements_as_values():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td)
        recording = r.Recording(path)
        seen = 0
        for _ts, _kind, plain in recording.packets():
            if struct.unpack_from('<h', plain, 0x74)[0] == 3:
                seen += 1
                if seen == 41:
                    with open(path, 'r+b') as f:
                        f.seek(recording.pos-len(plain)+0xF8)
                        f.write(struct.pack('<f', float('nan')))
                    break
        idx = fixture._index(path)
        class ShiftedTracks(fixture.FakeTracks):
            def project(self, points, oid):
                return [(d+.1) % 1000 for d in super().project(points, oid)]
        result = detail.window(path, idx, fixture._lap(idx, 3), ['clutch_engagement'],
                               'distance', 90, 120, ShiftedTracks())
        assert any(row['clutch_engagement'] is None for row in result['rows'])
        for row, state in zip(result['rows'], result['states'], strict=True):
            if row['clutch_engagement'] is None:
                assert state['clutch_engagement'] != 'value', 'an interpolated missing value must retain its unavailable state'


def t_corrupt_sparse_anchors_are_refused_before_reading_arbitrary_capture_bytes():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td)
        idx = fixture._index(path)
        lap = fixture._lap(idx, 3)
        for bad in [[[-1, -10]], [[0, r.Recording(path).header_end+1]], [['oops', 10]]]:
            altered = dict(idx, raw_seek=bad)
            try:
                detail.window(path, altered, lap, ['rpm'], 'time', 0, 1, fixture.FakeTracks())
            except ValueError as exc:
                assert 'channel index' in str(exc), 'corrupt seeks require an explicit index failure'
            except (OSError, TypeError, IndexError) as exc:
                raise AssertionError('invalid seek anchors require a channel index domain failure') from exc
            else:
                raise AssertionError('unverified seek anchors must not decode arbitrary bytes as telemetry')


def t_distance_time_provenance_follows_the_indexed_lap_quality():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td)
        idx = fixture._index(path)
        for n in (1, 3):
            lap = fixture._lap(idx, n)
            result = detail.window(path, idx, lap, ['rpm'], 'distance', 100, 200, fixture.FakeTracks())
            assert result['time_basis'] == lap['time_basis'], 'distance channels must retain the indexed time provenance'
            if n == 1:
                assert not lap['time_valid'] and 'normalized' not in result['time_basis']


def t_each_detail_resource_limit_reports_its_own_domain_failure():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td, lap_secs=(45, 45, 45, 45), n=400)
        idx = fixture._index(path)
        lap = fixture._lap(idx, 3)
        requests = [
            (['rpm']*0, 'time', 0, 1, 'between 1 and 8'),
            ([d['key'] for d in c.descriptors(False)[:9]], 'time', 0, 1, 'between 1 and 8'),
            (['rpm'], 'time', 0, 31, '30 seconds'),
            (['rpm'], 'time', float('nan'), 1, 'finite numbers'),
            (['rpm'], 'distance', 0, 1001, 'recorded lap')]
        for keys, axis, start, end, message in requests:
            try:
                detail.window(path, idx, lap, keys, axis, start, end, fixture.FakeTracks())
            except ValueError as exc:
                assert message in str(exc), 'each resource bound requires its own domain failure'
            else:
                raise AssertionError('a breached detail resource limit must fail before reading')
        real = detail.MAX_RAW_ROWS
        detail.MAX_RAW_ROWS = 2
        try:
            try:
                detail.window(path, idx, lap, ['rpm'], 'time', 0, 1, fixture.FakeTracks())
            except ValueError as exc:
                assert 'too many packets' in str(exc)
            else:
                raise AssertionError('raw detail must respect its packet output limit')
        finally:
            detail.MAX_RAW_ROWS = real


def t_seek_list_and_packet_scan_limits_fail_before_unbounded_work():
    with tempfile.TemporaryDirectory() as td:
        path = fixture.write_circle_recording(td)
        idx = fixture._index(path)
        lap = fixture._lap(idx, 3)
        limit = detail.MAX_SEEK_ROWS
        detail.MAX_SEEK_ROWS = 2
        try:
            try:
                detail.window(path, idx, lap, ['rpm'], 'time', 0, 1, fixture.FakeTracks())
            except ValueError as exc:
                assert 'seek list' in str(exc)
            else:
                raise AssertionError('channel seek lists must respect their resource bound')
        finally:
            detail.MAX_SEEK_ROWS = limit
        real = r.Recording
        plain = fixture._pkt(3, 50, 0)
        class FloodRecording(real):
            def packets(self, start=None):
                for _ in range(120001):
                    yield idx['start_ts']+lap['start_t_s']-1, '~', plain
        r.Recording = FloodRecording
        try:
            try:
                detail.window(path, idx, lap, ['rpm'], 'time', 0, 1, fixture.FakeTracks())
            except ValueError as exc:
                assert 'packet read limit' in str(exc)
            else:
                raise AssertionError('out-of-window packets must still respect the scan bound')
        finally:
            r.Recording = real


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('t_') and callable(fn):
            fn(); print('ok', name)
    print('ALL PASS')

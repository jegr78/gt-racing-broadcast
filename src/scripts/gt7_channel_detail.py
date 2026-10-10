#!/usr/bin/env python3
"""Bounded, selected post-session channels using the recording index's sparse seeks."""
import bisect
import math
import os
import gt7_channels

MAX_CHANNELS = 8
MAX_WINDOW_S = 30.0
MAX_RAW_ROWS = 5000
MAX_SEEK_ROWS = 100000
_BY_KEY = {d['key']: d for d in gt7_channels.descriptors()}

def window(path, index, lap, keys, axis='distance', start=0, end=None, tracks=None):
    """Selected channels only. Time windows retain packets; distance shares lap geometry."""
    import gt7_recording
    import gt7_laps
    keys = list(dict.fromkeys(keys))
    if not keys or len(keys) > MAX_CHANNELS or any(k not in _BY_KEY for k in keys):
        raise ValueError('select between 1 and 8 known telemetry channels')
    if axis not in ('time', 'distance'):
        raise ValueError('axis must be time or distance')
    end = (min(MAX_WINDOW_S, lap['end_t_s']-lap['start_t_s']) if axis == 'time'
           else lap.get('length_m')) if end is None else end
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (start, end)) or not 0 <= start < end:
        raise ValueError('window bounds must be finite and ordered')
    if axis == 'time' and end-start > MAX_WINDOW_S:
        raise ValueError('raw time windows must not exceed 30 seconds')
    extent = lap['end_t_s']-lap['start_t_s'] if axis == 'time' else lap.get('length_m')
    if (extent is None or not math.isfinite(extent) or end > extent + .002
            or axis == 'distance' and end > gt7_laps.MAX_TRACE_M):
        raise ValueError('window exceeds the recorded lap')
    seek = index.get('raw_seek') or []
    if not seek:
        raise ValueError('recording needs an updated channel index')
    recording = gt7_recording.Recording(path)
    size = os.path.getsize(path)
    if not isinstance(seek, list) or len(seek) > MAX_SEEK_ROWS:
        raise ValueError('invalid channel index seek list')
    previous_seek = None
    for point in seek:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or isinstance(point[0], bool) or not isinstance(point[0], (int, float))
                or not math.isfinite(point[0]) or point[0] < 0
                or isinstance(point[1], bool) or not isinstance(point[1], int)
                or not recording.header_end <= point[1] <= size-gt7_recording._REC.size
                or previous_seek and (point[0] <= previous_seek[0] or point[1] <= previous_seek[1])):
            raise ValueError('invalid channel index seek anchor')
        previous_seek = point
    target = lap['start_t_s'] + (start if axis == 'time' else 0)
    nearest = max(0, bisect.bisect_right([p[0] for p in seek], target)-1)
    offset = seek[nearest][1]
    with open(path, 'rb') as capture:
        capture.seek(offset)
        stamp, kind, length = gt7_recording._REC.unpack(capture.read(gt7_recording._REC.size))
    if (not math.isfinite(stamp) or abs(stamp-index['start_ts']-seek[nearest][0]) > .002
            or chr(kind) not in {'A', 'B', '~'} or length < gt7_recording._MIN_PAYLOAD
            or offset+gt7_recording._REC.size+length > size):
        raise ValueError('channel index anchor does not match the capture')
    base, low, high = index['start_ts'], lap['start_t_s'], lap['end_t_s']
    raw, previous, driven = [], None, 0.0
    inspect_keys = list(dict.fromkeys(keys + ['pos_x', 'pos_z', 'speed_kmh']))
    stop = low+end if axis == 'time' else high+0.2
    read = 0
    for ts, _kind, plain in recording.packets(offset):
        read += 1
        if read > 120000:
            raise ValueError('capture window exceeds the packet read limit')
        relative = ts-base
        if not math.isfinite(relative):
            continue
        if relative > stop:
            break
        if relative < low-.002:
            continue
        values = gt7_channels.decode(plain, inspect_keys)
        xy = (values['pos_x']['value'], values['pos_z']['value'])
        if axis == 'distance':
            if None in xy:
                continue
            if previous is not None:
                dt = max(0, ts-previous[0])
                speed = values['speed_kmh']['value']
                if speed is not None:
                    driven += max(0, speed/3.6)*dt
            previous = ts, xy
        elif relative < low+start:
            continue
        raw.append({'t': relative-low, 'd': driven if axis == 'distance' else None, 'x': xy[0], 'z': xy[1],
                    'values': {k: values[k] for k in keys}})
        if axis == 'time' and len(raw) > MAX_RAW_ROWS:
            raise ValueError('raw time window contains too many packets; reduce the window')
    distance_basis = 'unavailable on raw time axis'
    if axis == 'distance' and raw:
        track_id = lap.get('track_id')
        length = tracks.line_length(track_id) if tracks is not None and track_id else None
        projected = tracks.project([(p['x'], p['z']) for p in raw], track_id) if length else None
        if projected:
            ds = gt7_laps._follow(projected, [p['d'] for p in raw], length)
            distance_basis = 'reference geometry'
        else:
            ds = [p['d'] for p in raw]
            distance_basis = 'driven distance'
        for point, dist in zip(raw, ds, strict=True):
            point['d'] = dist
        # Do not fill uncovered stations or interpolate enum/flag/sentinel values.
        kept = []
        for point in raw:
            if not kept or point['d'] > kept[-1]['d']:
                kept.append(point)
        raw = kept
        stations = []
        if raw:
            distances = [p['d'] for p in raw]
            for i in range(math.ceil(start/gt7_laps.STEP_M), int(end//gt7_laps.STEP_M)+1):
                d = i*gt7_laps.STEP_M
                j = bisect.bisect_right(distances, d)-1
                if j < 0 or j+1 >= len(raw):
                    continue
                a, b = raw[j:j+2]
                f = (d-a['d'])/(b['d']-a['d'])
                values = {}
                for key in keys:
                    va, vb = a['values'][key], b['values'][key]
                    v = va['value']
                    if _BY_KEY[key]['type'] == 'continuous':
                        v = None if v is None or vb['value'] is None else v+(vb['value']-v)*f
                    state = va['state'] if va['state'] != 'value' else vb['state']
                    values[key] = {'value': v, 'state': state if v is None else 'value', 'raw': va['raw']}
                stations.append({'d': d, 't': a['t']+(b['t']-a['t'])*f,
                                 'x': a['x']+(b['x']-a['x'])*f, 'z': a['z']+(b['z']-a['z'])*f,
                                 'values': values})
        raw = stations
    availability = {}
    distance_time_basis = 'unavailable (indexed timing coverage missing)'
    if axis == 'distance':
        trace = lap.get('trace') or []
        if trace:
            distance_time_basis = lap.get('time_basis', 'receiver clock')
        distances = [p['d'] for p in trace]
        for point in raw:
            j = bisect.bisect_right(distances, point['d'])-1
            if 0 <= j < len(trace)-1:
                a, b = trace[j:j+2]
                point['t'] = a['t']+(b['t']-a['t'])*(point['d']-a['d'])/(b['d']-a['d'])
            else:
                point['t'] = None  # uncovered timing never inherits an unrelated raw time
    for key in keys:
        vals = [p['values'][key]['value'] for p in raw if p['values'][key]['value'] is not None]
        states = {state: sum(p['values'][key]['state'] == state for p in raw)
                  for state in ('value', 'missing', 'unset', 'nonfinite')}
        availability[key] = {'states': states, 'constant': bool(vals) and min(vals) == max(vals),
                             'min': min(vals) if vals else None, 'max': max(vals) if vals else None}
    return {'rows': [{k: p[k] for k in ('t', 'd', 'x', 'z')} |
                     {k: p['values'][k]['value'] for k in keys} for p in raw],
            'states': [{k: p['values'][k]['state'] for k in keys} for p in raw],
            'raw': [{k: p['values'][k]['raw'] for k in keys} for p in raw],
            'availability': availability, 'axis': axis, 'seek_offset': offset,
            'channels': [d for d in gt7_channels.descriptors() if d['key'] in keys], 'schema_version': gt7_channels.SCHEMA_VERSION,
            'time_basis': ('receiver clock, seconds from recorded lap boundary' if axis == 'time'
                           else distance_time_basis),
            'distance_basis': distance_basis}

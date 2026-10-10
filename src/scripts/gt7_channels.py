#!/usr/bin/env python3
"""Post-session packet channels and bounded detail from immutable recordings.

Own decoder based on the community packet layout documented by Nenkai PDTools
SimulatorPacket.cs at ffe0bb26377ac9ff62f40951c1c0b16c5d9a380f. Unverified meanings
and units remain diagnostics. No external code/data is required at runtime.
"""
import math
import struct

SCHEMA_VERSION = 1
SOURCE = 'PDTools SimulatorPacket.cs ffe0bb26377ac9ff62f40951c1c0b16c5d9a380f'
_CHANNELS = []


def _field(key, label, off, fmt='f', unit='1', *, factor=1, shift=0, mask=None,
           sentinel=None, diagnostic=False, uncertainty='', subtract=0):
    _CHANNELS.append(dict(key=key, label=label, offset=f'0x{off:03x}', encoding=fmt,
                          unit=unit, type='continuous' if fmt == 'f' and not diagnostic else 'step',
                          provenance='derived' if factor != 1 or subtract else 'packet',
                          source=SOURCE, diagnostic=diagnostic, uncertainty=uncertainty,
                          off=off, factor=factor, shift=shift, mask=mask,
                          sentinel=sentinel, subtract=subtract))


for group, off, label, unit in [('pos', 4, 'World position', 'm'),
                               ('velocity', 0x10, 'World velocity', 'm/s'),
                               ('angular_velocity', 0x2C, 'Angular velocity', 'rad/s')]:
    for i, axis in enumerate('xyz'):
        _field(group+'_'+axis, label+' '+axis.upper(), off+4*i, unit=unit)
for i, axis in enumerate('xyzw'):
    _field('rotation_'+axis, 'Rotation quaternion '+axis.upper(), 0x1C+4*i)
for key, label, off, unit in [
        ('body_height_m', 'Body height', 0x38, 'm'), ('rpm', 'Engine RPM', 0x3C, 'rpm'),
        ('fuel_l', 'Fuel level', 0x44, 'L'), ('fuel_capacity_l', 'Fuel capacity', 0x48, 'L'),
        ('oil_pressure_bar', 'Oil pressure', 0x54, 'bar'),
        ('water_temp_c', 'Water temperature', 0x58, '°C'),
        ('oil_temp_c', 'Oil temperature', 0x5C, '°C'),
        ('clutch_pedal', 'Clutch pedal', 0xF4, '1'),
        ('clutch_engagement', 'Clutch engagement', 0xF8, '1'),
        ('gearbox_rpm', 'Clutch-to-gearbox RPM', 0xFC, 'rpm'),
        ('transmission_top_ratio', 'Transmission top-speed ratio', 0x100, '1')]:
    _field(key, label, off, unit=unit)
_field('speed_kmh', 'Speed', 0x4C, unit='km/h', factor=3.6)
_field('boost_kpa', 'Boost relative to packet baseline', 0x50, unit='kPa', factor=100, subtract=1)
_field('steer_deg', 'Steering wheel angle', 0x128, unit='deg', factor=180/math.pi)
for prefix, off, label, unit in [('tyre', 0x60, 'Tyre surface temperature', '°C'),
                                 ('wheel', 0xA4, 'Wheel angular velocity', 'rad/s'),
                                 ('radius', 0xB4, 'Tyre radius', 'm')]:
    for i, corner in enumerate(('fl', 'fr', 'rl', 'rr')):
        suffix = '_c' if prefix == 'tyre' else '_rad_s' if prefix == 'wheel' else '_m'
        _field(prefix+'_'+corner+suffix, label+' '+corner.upper(), off+4*i, unit=unit)
for key, label, off, fmt, unit, sentinel in [
        ('packet_id', 'Packet sequence', 0x70, 'i', '1', None),
        ('lap', 'Game lap number', 0x74, 'h', '1', -1),
        ('race_laps', 'Game race laps', 0x76, 'h', '1', -1),
        ('best_time_ms', 'Game best lap time', 0x78, 'i', 'ms', -1),
        ('last_time_ms', 'Game last lap time', 0x7C, 'i', 'ms', -1),
        ('day_ms', 'Game time of day', 0x80, 'i', 'ms since midnight', None),
        ('pre_race_position', 'Pre-race position', 0x84, 'h', '1', -1),
        ('pre_race_cars', 'Pre-race car count', 0x86, 'h', '1', -1),
        ('alert_min_rpm', 'RPM display alert minimum', 0x88, 'h', 'rpm', None),
        ('alert_max_rpm', 'RPM display alert maximum', 0x8A, 'h', 'rpm', None),
        ('sim_flags', 'Simulator flags bitfield', 0x8E, 'H', 'bitfield', None),
        ('car_id', 'Car ID, overlaps gearbox data on 9+ gear cars', 0x124, 'i', 'ID', None)]:
    _field(key, label, off, fmt, unit, sentinel=sentinel)
_field('gear', 'Gear', 0x90, 'B', mask=15)
_field('suggested_gear', 'Suggested gear', 0x90, 'B', shift=4, mask=15, sentinel=15)
_field('throttle_pct', 'Throttle', 0x91, 'B', '%', factor=100/255)
_field('brake_pct', 'Brake', 0x92, 'B', '%', factor=100/255)
for bit, key, label in [
        (0, 'on_track', 'On track / paddock'), (1, 'paused', 'Simulation paused'),
        (2, 'loading', 'Loading / processing'), (4, 'has_turbo', 'Turbo present'),
        (5, 'rpm_alert_active', 'RPM display alert active'), (6, 'handbrake_active', 'Handbrake active'),
        (7, 'lights_active', 'Lights active'), (8, 'high_beam_active', 'High beam active'),
        (9, 'low_beam_active', 'Low beam active'), (10, 'asm_active', 'ASM active'),
        (11, 'tcs_active', 'TCS active')]:
    _field(key, label, 0x8E, 'H', shift=bit, mask=1)
for gear in range(1, 9):
    _field('gear_ratio_'+str(gear), 'Transmission ratio '+str(gear), 0x100+4*gear, sentinel=0)
# Every remaining field is inspectable without assigning unverified physical units.
for off, fmt, label in [(0, 'I', 'Packet magic'), (0x40, 'I', 'Packet IV'),
                        (0x8C, 'h', 'Calculated transmission speed, native unit unverified'),
                        (0x93, 'B', 'Reserved byte'),
                        *[(0x94+4*i, 'f', 'Road plane component, unit unverified') for i in range(3)],
                        (0xA0, 'f', 'Road plane distance, unit unverified'),
                        *[(0xC4+4*i, 'f', 'Suspension height, unit unverified') for i in range(4)],
                        *[(off, 'f', 'Reserved float interpretation') for off in range(0xD4, 0xF4, 4)],
                        (0x12C, 'f', 'Unknown extended float'),
                        *[(off, 'f', name+' motion, unit unverified') for off, name in
                          [(0x130, 'Sway'), (0x134, 'Heave'), (0x138, 'Surge')]],
                        (0x13C, 'B', 'Pedal byte, assist/filter interpretation disputed'),
                        (0x13D, 'B', 'Pedal byte, assist/filter interpretation disputed'),
                        (0x13E, 'B', 'Unknown car-type byte'), (0x13F, 'B', 'Unknown consumption byte'),
                        *[(off, 'f', 'Unknown extended component') for off in range(0x140, 0x158, 4)]]:
    _field('raw_'+f'0x{off:03x}', label, off, fmt, 'native', diagnostic=True,
           uncertainty='Raw representation only; interpretation/unit is not validated')
for bit in (3, 12, 13, 14, 15):
    _field('raw_flag_'+str(bit), 'Unverified simulator flag '+str(bit), 0x8E, 'H',
           shift=bit, mask=1, diagnostic=True, uncertainty='Flag meaning is unverified')
_BY_KEY = {d['key']: d for d in _CHANNELS}


def descriptors(diagnostic=None):
    """Public descriptors are copies; no internal decoder configuration leaks or mutates."""
    hidden = {'off', 'factor', 'shift', 'mask', 'subtract'}
    return [{k: v for k, v in d.items() if k not in hidden} for d in _CHANNELS
            if diagnostic is None or d['diagnostic'] == diagnostic]


def decode(plain, keys=None):
    """Keep zero, unavailable bytes, declared sentinels and nonfinite floats distinct."""
    keys = list(_BY_KEY) if keys is None else list(keys)
    if any(key not in _BY_KEY for key in keys):
        raise ValueError('unknown telemetry channel')
    out = {}
    for key in keys:
        d = _BY_KEY[key]
        off, size = d['off'], struct.calcsize('<'+d['encoding'])
        if len(plain) < off + size:
            out[key] = {'value': None, 'state': 'missing', 'raw': None}
            continue
        raw = plain[off:off+size].hex()
        v = struct.unpack_from('<'+d['encoding'], plain, off)[0]
        if d['mask'] is not None:
            v = (v >> d['shift']) & d['mask']
        state = 'nonfinite' if not math.isfinite(v) else 'unset' if v == d['sentinel'] else 'value'
        out[key] = {'value': (v-d['subtract'])*d['factor'] if state == 'value' else None,
                    'state': state, 'raw': raw}
    return out


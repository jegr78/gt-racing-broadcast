#!/usr/bin/env python3
"""Wheel rotation, tyre radius, slip ratio and lock-up/wheelspin events (#1106).
Run: python3 tests/test_gt7_wheel_slip.py"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_gt7_telemetry import _packet, tm  # noqa: E402

R = 0.36


def wheel_packet(speed, rot=None, radius=(R,) * 4, throttle=0, brake=0):
    """A packet whose wheels turn `rot` rad/s; by default free-rolling at `speed`."""
    b = bytearray(_packet(speed_mps=speed, throttle=throttle, brake=brake))
    rot = rot if rot is not None else (speed / R,) * 4
    struct.pack_into("<4f", b, tm.OFF_WHEEL_RPS, *rot)
    struct.pack_into("<4f", b, tm.OFF_TYRE_RADIUS, *radius)
    return tm.parse_packet(bytes(b))


def t_parse_exposes_rotation_radius_and_slip():
    p = wheel_packet(30.0, rot=(30 / R, -30 / R, 15 / R, 45 / R))
    assert [round(v, 3) for v in p.wheel_rps] == [83.333, -83.333, 41.667, 125.0], p.wheel_rps
    assert p.tyre_radius == (R,) * 4 or all(abs(a - R) < 1e-6 for a in p.tyre_radius)
    assert [round(s, 3) for s in p.slip] == [1.0, 1.0, 0.5, 1.5], \
        "slip is |rotation| x radius / car speed, a reversed wheel turns the same"


def t_slip_is_undefined_below_the_minimum_speed():
    assert wheel_packet(tm.SLIP_MIN_SPEED_MPS - 0.1).slip is None
    assert wheel_packet(tm.SLIP_MIN_SPEED_MPS + 0.1).slip is not None


def t_a_short_packet_has_no_wheel_fields():
    p = tm.parse_packet(_packet(speed_mps=30.0)[:0xA0])
    assert (p.wheel_rps, p.tyre_radius, p.slip) == (None, None, None)


def run(detector, packets, t0=0.0, dt=1 / 60):
    t = t0
    for p in packets:
        detector.update(p, t)
        t += dt
    return detector.close(t)


def t_a_braked_wheel_below_the_floor_is_a_lockup():
    locked = (30 / R, 30 / R * 0.2, 30 / R, 30 / R)      # FR turns at 20 % of the car speed
    d = tm.SlipEvents()
    packets = ([wheel_packet(30)] * 10 + [wheel_packet(30, rot=locked, brake=200)] * 30
               + [wheel_packet(30)] * 10)
    events = run(d, packets)
    assert len(events) == 1, events
    e = events[0]
    assert (e["kind"], e["wheel"]) == ("lockup", "FR"), e
    assert abs(e["start_s"] - 10 / 60) < 1e-6 and abs(e["duration_s"] - 30 / 60) < 1e-6, e
    assert round(e["extreme"], 3) == 0.2, e


def t_a_driven_wheel_above_the_ceiling_under_throttle_is_wheelspin():
    spin = (30 / R, 30 / R, 30 / R * 1.6, 30 / R)
    events = run(tm.SlipEvents(), [wheel_packet(30, rot=spin, throttle=255)] * 30)
    assert [(e["kind"], e["wheel"]) for e in events] == [("wheelspin", "RL")], events
    assert round(events[0]["extreme"], 3) == 1.6


def t_a_spike_shorter_than_the_minimum_duration_is_no_event():
    spin = (30 / R, 30 / R, 30 / R * 1.6, 30 / R)
    n = int(tm.SLIP_EVENT_MIN_S * 60) - 1
    events = run(tm.SlipEvents(), [wheel_packet(30)] * 5 + [wheel_packet(30, rot=spin, throttle=255)] * n
                 + [wheel_packet(30)] * 5)
    assert events == [], events


def t_slip_without_the_pedal_is_no_event():
    locked = (30 / R * 0.2,) * 4
    assert run(tm.SlipEvents(), [wheel_packet(30, rot=locked)] * 30) == [], \
        "a lock-up needs the brake, wheelspin the throttle"
    spin = (30 / R * 1.6,) * 4
    assert run(tm.SlipEvents(), [wheel_packet(30, rot=spin, brake=100)] * 30) == []


def t_the_low_speed_cutoff_ends_an_event():
    locked = (30 / R, 30 / R * 0.2, 30 / R, 30 / R)
    d = tm.SlipEvents()
    events = run(d, [wheel_packet(30, rot=locked, brake=200)] * 30
                 + [wheel_packet(5, rot=(0,) * 4, brake=200)] * 30)
    assert len(events) == 1 and abs(events[0]["duration_s"] - 0.5) < 1e-6, \
        f"below the minimum speed slip is undefined, the event ends: {events}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

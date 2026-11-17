#!/usr/bin/env python3
"""The maintainer driver replay (#1115). Run: python3 tests/test_driver_replay.py"""
import importlib.util, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_crypto  # noqa: E402
import gt7_telemetry as tm  # noqa: E402
from test_gt7_telemetry import _packet  # noqa: E402

spec = importlib.util.spec_from_file_location("driver_replay", os.path.join(ROOT, "tools", "driver-replay.py"))
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def t_replay_packets_carry_what_the_race_reads():
    mine = gt7_crypto.decrypt_typed(replay.packet(12, 3, 50.0, 1.5, -2.0, 61000, 60000, 40.0))[1]
    ref = bytearray(_packet(lap=3, speed_mps=50.0, pos=(1.5, 0.0, -2.0), last_ms=61000,
                            best_ms=60000, fuel_level=40.0))
    struct.pack_into("<i", ref, 0x70, 12)
    for name in ("OFF_LAP", "OFF_LAST_MS", "OFF_BEST_MS", "OFF_POS", "OFF_FUEL_LEVEL", "OFF_SPEED", "OFF_FLAGS"):
        off = getattr(tm, name)
        assert mine[off:off + 4] == bytes(ref[off:off + 4]), f"{name} differs from a GT7 packet"
    assert struct.unpack_from("<i", mine, 0x70)[0] == 12, "the packet id dates the sample"


def t_the_line_wraps_at_its_length():
    line = replay.Line([{"d": 0.0, "x": 0.0, "z": 0.0}, {"d": 100.0, "x": 100.0, "z": 0.0}])
    assert line.at(25.0) == (25.0, 0.0) and line.at(125.0) == (25.0, 0.0) and line.at(-10.0) == (90.0, 0.0)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

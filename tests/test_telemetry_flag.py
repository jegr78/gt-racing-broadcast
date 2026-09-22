#!/usr/bin/env python3
"""Stdlib unit checks for the GT7 telemetry enable flag (#324). Run: python3 tests/test_telemetry_flag.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_telemetry_enabled_default_on():
    # Default ON: absent or empty runs the telemetry listener, and solo idles
    # harmlessly when no console answers.
    assert m.telemetry_enabled({}) is True
    assert m.telemetry_enabled({"RACECAST_GT7_TELEMETRY": ""}) is True


def t_telemetry_enabled_explicit_falsey_disables():
    for v in ("0", "false", "no", "off", "OFF", "False"):
        assert m.telemetry_enabled({"RACECAST_GT7_TELEMETRY": v}) is False, v


def t_telemetry_enabled_truthy_token():
    assert m.telemetry_enabled({"RACECAST_GT7_TELEMETRY": "1"}) is True


def t_telemetry_active_pov_only():
    # POV solo, template=pov, runs telemetry.
    assert m.telemetry_active(True, {"RACECAST_TEMPLATE": "pov"}) is True
    assert m.telemetry_active(True, {"RACECAST_TEMPLATE": "POV"}) is True
    # Commentary solo has no console, so no telemetry and no empty block.
    assert m.telemetry_active(True, {"RACECAST_TEMPLATE": "commentary"}) is False
    # Solo with the template unset is off; POV must be explicit.
    assert m.telemetry_active(True, {}) is False
    # Endurance is off whatever the template says.
    assert m.telemetry_active(False, {"RACECAST_TEMPLATE": "pov"}) is False
    # POV solo, explicitly disabled, is off.
    assert m.telemetry_active(True, {"RACECAST_TEMPLATE": "pov",
                                     "RACECAST_GT7_TELEMETRY": "0"}) is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

"""Keep a test process off the machine's OBS.

A producer machine runs OBS with obs-websocket on 4455, and the relay's OBS helpers
connect to it with the password from OBS's own config. A test that reaches them can
switch the live program, mute the commentary mic or hold sessions open. install()
swaps the relay module's `_obs_ws` for NoObs and trips the real module's connect
function, so any real connection attempt fails the test instead of reaching OBS.

Each file under tests/ runs in its own process (tools/run-tests.py), so the
module-level patch never leaks into another test file.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import obs_ws as _real  # noqa: E402

NOTE = "no OBS in tests"

# What each OBS call returns when OBS is unreachable: the shape the relay handles.
_UNREACHABLE = {
    "ensure_mic_device": {"state": None, "device": None, "note": NOTE},
    "feed_media_cursors": ({}, NOTE),
    "get_current_program_scene": (None, NOTE),
    "get_health_stats": (False, {}, NOTE),
    "get_program_screenshot": (None, NOTE),
    "get_scene_collection": (None, NOTE),
    "get_source_screenshot": (None, NOTE),
    "probe": (False, NOTE),
    "probe_device_options": ([], NOTE),
    "read_obs_state": (None, NOTE),
    "reflect_feed_state": ([], NOTE),
    "refresh_browser_inputs": ([], NOTE),
    "release_feed_inputs": ([], NOTE),
    "set_current_program_scene": (False, NOTE),
    "set_feed_close_when_inactive": NOTE,
    "set_input_mute": (False, NOTE),
    "set_input_volume": (False, NOTE),
    "set_scene_collection": (False, NOTE),
    "set_scene_item_enabled": (False, NOTE),
    "set_scene_item_transform": (False, NOTE),
    "set_stream": (False, NOTE),
    "set_stream_service": (False, NOTE),
    "switch_to_scene_if_idle": ("error", NOTE),
    "tune_feed_inputs": ([], NOTE),
}


class NoObs:
    """obs_ws stand-in: every call that would talk to OBS answers as if OBS were
    unreachable; constants and pure helpers come from the real module. It has no
    `route_kind`, so the relay's facade calls it directly and never opens its
    persistent connections."""

    def __getattr__(self, name):
        if name == "route_kind" or name.startswith("_"):
            raise AttributeError(name)
        if name in _UNREACHABLE:
            result = _UNREACHABLE[name]
            return lambda *a, **k: result
        return getattr(_real, name)          # constants + pure helpers


# Every real connection attempt, recorded here too: the relay's OBS helpers catch
# broad exceptions on purpose (best effort), so the raise alone could be swallowed.
CALLS = []


def _tripwire(*a, **k):
    CALLS.append((a, k))
    raise AssertionError("a test tried to connect to a real OBS")


def is_tripped():
    return _real._connect is _tripwire and _real._open_session is _tripwire


def install(module):
    """Point `module._obs_ws` at NoObs and trip every real OBS connection."""
    module._obs_ws = NoObs()
    _real._connect = _tripwire
    _real._open_session = _tripwire

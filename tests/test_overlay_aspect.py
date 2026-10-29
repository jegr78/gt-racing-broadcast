#!/usr/bin/env python3
"""Aspect settings are persisted editor metadata, never generated CSS."""
import math
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src/scripts'), str(ROOT / 'tests')]
import overlay_build as ob  # noqa: E402
import test_racecast as cli  # noqa: E402


def t_video_defaults_are_declared_by_the_actual_markup():
    for page, expected in [('hud', {'webcam', 'tyres-capture'}),
                           ('intermission', {'intermission-webcam', 'intermission-capture'}),
                           ('splitscreen', set())]:
        slots = ob.extract_slots((ROOT / 'src/obs' / (page + '.html')).read_text())
        assert {s['id'] for s in slots if s.get('lockAspect')} == expected
        assert all({'width', 'height'} <= set(s['props']) for s in slots if s.get('lockAspect'))


def t_aspect_metadata_validation_rejects_invalid_and_nonfinite_values():
    for value in [None, False, 16/9, '16:9', [], [1], [1, 2, 3], [0, 1], [-1, 1],
                  [True, 1], [1, False], ['16', 9], [math.inf, 1], [math.nan, 1],
                  [1e308, 1e-308], [1e-308, 1e308], [10**400, 1]]:
        assert not ob.valid_aspect_settings({'aspectRatio': value}), value
    for value in [0, 1, 'true', None, []]:
        assert not ob.valid_aspect_settings({'lockAspect': value}), value
    for value in [{}, {'lockAspect': False}, {'lockAspect': True, 'aspectRatio': [245, 84]},
                  {'aspectRatio': [1.5, 1]}, {'aspectRatio': [1e-300, 1e-300]}]:
        assert ob.valid_aspect_settings(value), value


def t_editor_metadata_never_becomes_css_or_changes_saved_geometry():
    slots = ob.extract_slots('<div id="video" data-edit="Video" data-edit-kind="box"></div>')
    layout = {'slots': {'video': {'width': 490, 'height': 168, 'lockAspect': True,
                                 'aspectRatio': [245, 84]}}}
    css = ob.compile_overlay_css(layout, slots)
    assert 'width: 490px' in css and 'height: 168px' in css
    assert 'aspect' not in css.lower() and '245' not in css


def t_layout_round_trip_retains_aspect_choice_and_rejects_bad_metadata_atomically():
    m = cli.m
    with tempfile.TemporaryDirectory() as td, patch.dict('os.environ', {'RACECAST_PROFILE': 'demo'}):
        original = cli._mk_active_profile(td)
        try:
            layout = {'page': 'hud', 'slots': {'webcam': {'width': 400, 'height': 225,
                      'lockAspect': False, 'aspectRatio': [16, 9]}}}
            result = m.overlay_layout_write_data('hud', layout)
            assert result['ok'], result
            saved = m.overlay_layout_read_data('hud')['layout']
            assert saved['slots']['webcam'] == layout['slots']['webcam']
            path = Path(td) / 'profiles/demo/overlay/layout-hud.json'
            before = path.read_bytes()
            for bad in [{'lockAspect': 'true'}, {'aspectRatio': [0, 9]},
                        {'aspectRatio': [16, math.inf]}, {'aspectRatio': {'width': 16}}]:
                denied = m.overlay_layout_write_data('hud', {'slots': {'webcam': bad}})
                assert not denied['ok'], denied
                assert path.read_bytes() == before
        finally:
            m._env_base, m._runtime_base_dir = original


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('t_') and callable(fn):
            fn()
            print('ok', name)
    print('ALL PASS')

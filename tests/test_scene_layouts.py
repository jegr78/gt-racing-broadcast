"""Scene-specific builder contracts, including real profile persistence and OBS intent."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import test_racecast as cli
import test_solo_obs as solo_obs
import test_ui_server as ui

m = cli.m
ob = m.ob
ROOT = Path(__file__).resolve().parents[1]


def t_intermission_slots_follow_profile_kind():
    for solo in (False, True):
        with patch.object(m, '_profile_is_solo', return_value=solo):
            data = m.overlay_slots_data('intermission')
        assert data['ok'], data
        ids = {slot['id'] for slot in data['slots']}
        assert 'ichat' in ids
        assert ('intermission-webcam' in ids) == solo
        assert ('intermission-capture' in ids) == solo
        assert 'fontSize' in next(s for s in data['slots'] if s['id'] == 'ichat')['props']
        assert 'intermission-webcam' not in {s['id'] for s in m.overlay_slots_data('hud')['slots']}


def t_intermission_layout_is_independent_and_migrates_legacy_css():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        od = Path(td) / 'profiles/demo/overlay'
        od.mkdir()
        (od / 'hud.css').write_text('#clock{left:30px}', encoding="utf-8")
        (od / 'intermission.css').write_text('#ichat{color:gold}', encoding="utf-8")
        try:
            loaded = m.overlay_layout_read_data('intermission')
            assert loaded['ok'] and loaded['migrated'], loaded
            assert loaded['layout']['customCss'] == '#ichat{color:gold}'
            saved = m.overlay_layout_write_data('intermission', {
                'page': 'intermission', 'slots': {'ichat': {'left': 80, 'width': 500,
                                                         'visible': False}},
                'customCss': loaded['layout']['customCss']})
            assert saved['ok'], saved
            css = (od / 'intermission.css').read_text(encoding="utf-8")
            assert 'left: 80px' in css and 'width: 500px' in css and 'display: none' in css
            assert css.endswith('#ichat{color:gold}')
            assert (od / 'hud.css').read_text(encoding="utf-8") == '#clock{left:30px}'
            assert m.overlay_layout_read_data('intermission')['layout']['slots']['ichat']['left'] == 80
            assert not m.overlay_layout_write_data('intermission', {'page': 'hud'})['ok']
            assert not m.overlay_layout_write_data('../intermission', {})['ok']
        finally:
            m._env_base, m._runtime_base_dir = original


def t_intermission_bake_changes_only_its_nested_video_items():
    path = ROOT / 'src/obs/GT_Racing_Solo_POV.json'
    collection = json.loads(path.read_text(encoding="utf-8"))
    original = json.loads(path.read_text(encoding="utf-8"))
    html = (ROOT / 'src/obs/intermission.html').read_text(encoding="utf-8")
    css = '#intermission-webcam{left:200px;top:100px;width:640px;height:360px;display:none}'
    solo_obs.sa.bake_scene_layout(collection, 'intermission', html, css)
    for before, after in zip(original['sources'], collection['sources'], strict=True):
        if before['name'] != 'Intermission':
            assert before == after, before['name']
    scene = next(s for s in collection['sources'] if s['name'] == 'Intermission')
    camera = next(i for i in scene['settings']['items'] if i['name'] == 'Solo Webcam')
    assert camera['pos'] == {'x': 200, 'y': 100}
    assert camera['bounds'] == {'x': 640, 'y': 360}
    assert camera['visible'] is False


def t_explicit_apply_targets_scene_and_automatic_sync_never_shows_camera():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        od = Path(td) / 'profiles/demo/overlay'
        od.mkdir()
        (od / 'intermission.css').write_text(
            '#intermission-webcam{left:90px;top:100px;width:800px;height:450px}'
            '#intermission-capture{display:none}', encoding="utf-8")
        transforms, visibility = [], []
        try:
            with patch.object(m, '_profile_is_solo', return_value=True):
                result = m.overlay_apply_data('intermission',
                    set_transform=lambda *a: (transforms.append(a) or (True, '')),
                    set_enabled=lambda *a: (visibility.append(a) or (True, '')),
                    refresh=lambda **k: (['Intermission Chat'], ''), restore=lambda: {'ok': True})
                assert result['ok'], result
                assert all(a[0] == 'Intermission' for a in transforms + visibility)
                assert ('Intermission', 'Solo Webcam', True) in visibility
                assert ('Intermission', 'Solo Capture', False) in visibility
                visibility.clear()
                m._sync_scene_layouts(set_transform=lambda *a: (True, ''),
                    set_enabled=lambda *a: (visibility.append(a) or (True, '')), solo=True)
                assert ('Intermission', 'Solo Webcam', True) not in visibility
                failed = m.overlay_apply_data('intermission',
                    set_transform=lambda *a: (False, 'OBS disconnected'),
                    set_enabled=lambda *a: (False, 'OBS disconnected'),
                    refresh=lambda **k: ([], 'OBS disconnected'))
                assert failed['ok'] is False and failed['error']
                assert m.overlay_apply_data('../hud', refresh=lambda **kw: (['Intermission Chat'], '')) == {
                    'ok': False, 'error': 'no active profile or invalid page'}
        finally:
            m._env_base, m._runtime_base_dir = original


def t_scene_background_does_not_reuse_hud_graphic():
    with tempfile.TemporaryDirectory() as td:
        Path(td, 'Overlay.png').write_bytes(b'hud')
        Path(td, 'Intermission.png').write_bytes(b'break')
        with patch.object(m, '_asset_dirs', return_value=(td, td)):
            assert m.overlay_bg_path('intermission') == os.path.join(td, 'Intermission.png')
            assert m.overlay_bg_path() == os.path.join(td, 'Overlay.png')
            assert m.overlay_bg_path('../Overlay') is None


def t_scene_http_routes_report_validation_errors_and_preserve_hud():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        ctx = ui._ctx()
        ctx.update(overlay_layout_write=m.overlay_layout_write_data,
                   overlay_layout_read=m.overlay_layout_read_data,
                   overlay_slots=m.overlay_slots_data,
                   overlay_apply=lambda page: m.overlay_apply_data(page,
                       refresh=lambda **kw: ([], 'OBS disconnected')))
        server, port = ui._serve(ctx)
        try:
            status, body = ui._post_json(port, '/api/overlay/layout', {
                'page': 'intermission', 'layout': {'page': 'intermission',
                    'slots': {'ichat': {'left': 140}}}})
            assert status == 200 and json.loads(body)['ok']
            status, body = ui._post_json(port, '/api/overlay/layout', {
                'page': 'intermission', 'layout': {'page': 'hud'}})
            assert status == 400 and json.loads(body) == {'ok': False, 'error': 'layout page mismatch'}
            status, body = ui._post_json(port, '/api/overlay/apply', {'page': 'intermission'})
            assert status == 400 and json.loads(body)['error'] == 'OBS disconnected'
            assert not (Path(td) / 'profiles/demo/overlay/hud.css').exists()
        finally:
            server.shutdown()
            server.server_close()
            m._env_base, m._runtime_base_dir = original


def t_intermission_layout_survives_bundle_and_backup_roundtrips():
    import backup_admin
    import profile_io
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            assert m.overlay_layout_write_data('intermission', {
                'slots': {'ichat': {'left': 123, 'visible': False}}})['ok']
            profile = Path(td) / 'profiles/demo'
            expected = (profile / 'overlay/intermission.css').read_bytes()
            archive = profile_io.export_profile('demo', {'profile_dir': str(profile)}, False,
                                                str(Path(td) / 'profile.zip'))
            dest = Path(td) / 'destination'
            dest.mkdir()
            profile_io.import_profile(archive, {'profiles_root': str(dest / 'profiles'),
                                                'runtime_root': str(dest / 'runtime')})
            assert (dest / 'profiles/demo/overlay/intermission.css').read_bytes() == expected
            sources = {'overlay': str(profile / 'overlay'), 'backups': str(Path(td) / 'backups')}
            backup = backup_admin.create_backup('Scene', sources, 'demo')
            (profile / 'overlay/intermission.css').write_text('changed', encoding="utf-8")
            backup_admin.restore_backup(backup, sources)
            assert (profile / 'overlay/intermission.css').read_bytes() == expected
        finally:
            m._env_base, m._runtime_base_dir = original


def t_apply_refuses_a_foreign_running_profile_before_obs_mutation():
    calls = []
    with patch.object(m, '_active_profile_name', return_value='demo'), \
            patch.object(m, '_foreign_relay_profile', return_value='another-league'):
        result = m.overlay_apply_data('intermission',
            set_transform=lambda *a: calls.append(a),
            set_enabled=lambda *a: calls.append(a),
            refresh=lambda **kw: calls.append(kw))
    assert result['ok'] is False and 'another-league' in result['error']
    assert not calls


def t_legacy_intermission_css_leaves_video_defaults_and_endurance_untouched():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        od = Path(td) / 'profiles/demo/overlay'
        od.mkdir()
        (od / 'intermission.css').write_text('#ichat{right:20px;width:500px}', encoding="utf-8")
        calls = []
        try:
            result = m._sync_scene_layouts(solo=True, explicit=True,
                set_transform=lambda *a: calls.append(a),
                set_enabled=lambda *a: calls.append(a))
            assert result['ok'] and not calls
            with patch.object(m, '_profile_is_solo', return_value=False):
                result = m.overlay_apply_data('intermission',
                    set_transform=lambda *a: calls.append(a),
                    set_enabled=lambda *a: calls.append(a),
                    refresh=lambda **kw: (['Intermission Chat'], ''), restore=lambda: {'ok': True})
            assert result['ok'] and not calls
        finally:
            m._env_base, m._runtime_base_dir = original


if __name__ == '__main__':
    for name, fn in sorted(globals().copy().items()):
        if name.startswith('t_') and callable(fn):
            fn()
            print('ok', name)
    print('ALL PASS')

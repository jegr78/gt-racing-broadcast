"""Splitscreen layout contracts."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import test_racecast as cli
import test_solo_obs
import test_console_gate as gate  # installs the real-OBS tripwire before any test
import obs_ws

m = cli.m
ROOT = Path(__file__).resolve().parents[1]


def t_split_slots_are_endurance_only_and_show_actual_scene_components():
    with patch.object(m, '_profile_is_solo', return_value=False):
        data = m.overlay_slots_data('splitscreen')
    assert data['ok'], data
    slots = {slot['id']: slot for slot in data['slots']}
    assert {'split-feed-a', 'split-feed-b', 'split-left', 'split-right', 'clock'} <= set(slots)
    assert 'visible' not in slots['split-feed-a']['props'], 'Live split control owns video visibility'
    assert 'offsetRight' in slots['split-left']['props']
    assert 'webcam' not in slots and 'tyres-capture' not in slots
    assert len(data['templates']) >= 2
    with patch.object(m, '_profile_is_solo', return_value=True):
        denied = m.overlay_slots_data('splitscreen')
    assert denied['ok'] is False and denied['error'], denied


def t_split_first_edit_seeds_hud_and_legacy_labels_then_persists_independently():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        overlay = Path(td) / 'profiles/demo/overlay'
        overlay.mkdir()
        hud = {'version': 1, 'page': 'hud', 'slots': {'clock': {'left': 42}},
               'fonts': [], 'customCss': '#clock{color:gold}'}
        (overlay / 'layout-hud.json').write_text(json.dumps(hud), encoding='utf-8')
        (overlay / 'hud.css').write_text('#clock{left:42px;color:gold}', encoding='utf-8')
        legacy = '#split-left{background:blue}'
        (overlay / 'splitscreen.css').write_text(legacy, encoding='utf-8')
        before = {name: (overlay / name).read_bytes() for name in ('layout-hud.json', 'hud.css')}
        try:
            with patch.object(m, '_profile_is_solo', return_value=False):
                read = m.overlay_layout_read_data('splitscreen')
                assert read['ok'] and read['migrated'], read
                layout = read['layout']
                assert layout['page'] == 'splitscreen'
                assert layout['slots']['clock']['left'] == 42
                assert '#clock{color:gold}' in layout['customCss'] and legacy in layout['customCss']
                layout['slots'].update({'clock': {'left': 300},
                    'split-feed-a': {'left': 80, 'top': 100, 'width': 1200, 'height': 675},
                    'split-left': {'offsetRight': 24, 'offsetTop': -40, 'visible': False}})
                saved = m.overlay_layout_write_data('splitscreen', layout)
                assert saved['ok'], saved
                assert m.overlay_layout_read_data('splitscreen')['layout']['slots']['clock']['left'] == 300
            assert all((overlay / name).read_bytes() == value for name, value in before.items())
            css = (overlay / 'splitscreen.css').read_text(encoding='utf-8')
            assert 'margin-right: 24px' in css and 'display: none' in css
        finally:
            m._env_base, m._runtime_base_dir = original


def t_split_collection_uses_an_independent_hud_browser_and_scoped_video_bounds():
    collection = json.loads((ROOT / 'src/obs/GT_Racing_Endurance.json').read_text(encoding='utf-8'))
    original = json.loads(json.dumps(collection))
    browsers = {s['name']: s for s in collection['sources'] if s.get('id') == 'browser_source'}
    assert browsers['HUD Overlay']['settings']['url'].endswith('/hud')
    assert 'Splitscreen HUD Overlay' in browsers, 'Split requires its own HUD browser'
    assert browsers['Splitscreen HUD Overlay']['settings']['url'].endswith('/splitscreen/hud')
    for group in collection['groups']:
        names = {item['name'] for item in group['settings']['items']}
        if group['name'] == 'Split HUD':
            assert 'Splitscreen HUD Overlay' in names and 'HUD Overlay' not in names
        elif group['name'] == 'Stint HUD':
            assert 'HUD Overlay' in names and 'Splitscreen HUD Overlay' not in names
    with patch.object(m, '_profile_is_solo', return_value=False):
        html = m._overlay_base_html('splitscreen')
    scene = next(x for x in collection['sources'] if x['name'] == 'Splitscreen')
    feed = next(x for x in scene['settings']['items'] if x['name'] == 'Feed A')
    feed.update(bounds_type=0, align=0, bounds_align=5, visible=False)
    css = '#split-feed-a{left:80px;top:100px;width:1200px;height:675px}'
    test_solo_obs.sa.bake_scene_layout(collection, 'splitscreen', html, css)
    for before, after in zip(original['sources'], collection['sources'], strict=True):
        if before['name'] != 'Splitscreen':
            assert before == after, before['name']
    scene = next(s for s in collection['sources'] if s['name'] == 'Splitscreen')
    feed = next(i for i in scene['settings']['items'] if i['name'] == 'Feed A')
    assert feed['pos'] == {'x': 80, 'y': 100}
    assert feed['bounds'] == {'x': 1200, 'y': 675}
    assert feed['bounds_type'] == 2 and feed['visible'] is False
    assert feed['align'] == 5 and feed['bounds_align'] == 0


def t_split_live_apply_moves_feed_and_existing_cover_without_visibility_or_audio():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        overlay = Path(td) / 'profiles/demo/overlay'
        overlay.mkdir()
        (overlay / 'splitscreen.css').write_text(
            '#split-feed-a{left:80px;top:100px;width:1200px;height:675px}'
            '#split-feed-b{left:1340px;top:100px;width:480px;height:270px}', encoding='utf-8')
        transforms, forbidden = [], []
        try:
            with patch.object(m, '_profile_is_solo', return_value=False), patch.object(
                    obs_ws, 'get_scene_items', return_value=({'Feed A Cover': False}, '')):
                result = m.overlay_apply_data('splitscreen',
                    set_transform=lambda *args: (transforms.append(args) or (True, '')),
                    set_enabled=lambda *args: (forbidden.append(args) or (True, '')),
                    refresh=lambda **kwargs: (['Splitscreen Labels', 'Splitscreen HUD Overlay'], ''))
            assert result['ok'], result
            assert {(a[0], a[1]) for a in transforms} == {
                ('Splitscreen', 'Feed A'), ('Splitscreen', 'Feed B'), ('Splitscreen', 'Feed A Cover')}
            assert forbidden == [], forbidden
            assert transforms[0][2]['boundsType'] == 'OBS_BOUNDS_SCALE_INNER'
        finally:
            m._env_base, m._runtime_base_dir = original


def t_solo_rejects_split_read_write_apply_before_any_mutation():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        calls = []
        try:
            with patch.object(m, '_profile_is_solo', return_value=True):
                answers = [m.overlay_layout_read_data('splitscreen'),
                    m.overlay_layout_write_data('splitscreen', {'slots': {}}),
                    m.overlay_apply_data('splitscreen',
                        refresh=lambda **kw: (calls.append(kw) or ([], '')),
                        set_transform=lambda *a: (calls.append(a) or (True, '')))]
            assert all(not a['ok'] and a['error'] for a in answers), answers
            assert not calls
            assert not (Path(td) / 'profiles/demo/overlay').exists()
        finally:
            m._env_base, m._runtime_base_dir = original


def t_split_apply_explains_old_collection_and_disconnected_obs():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            with patch.object(m, '_profile_is_solo', return_value=False):
                calls = []
                result = m.overlay_apply_data('splitscreen',
                    set_transform=lambda *args: calls.append(args),
                    refresh=lambda **kw: (['Splitscreen Labels'], ''))
                assert not calls, 'Old collections must be diagnosed before video geometry changes'
                assert not result['ok'] and 'collection' in result['error'].lower(), result
                result = m.overlay_apply_data('splitscreen',
                    refresh=lambda **kw: ([], 'OBS disconnected'))
                assert not result['ok'] and 'OBS disconnected' in result['error'], result
        finally:
            m._env_base, m._runtime_base_dir = original


def t_split_layout_survives_profile_bundle_and_look_restore():
    import backup_admin
    import profile_io
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            with patch.object(m, '_profile_is_solo', return_value=False):
                saved = m.overlay_layout_write_data('splitscreen', {
                    'slots': {'split-feed-b': {'left': 1400, 'top': 60, 'width': 400, 'height': 225},
                              'split-right': {'offsetRight': 18, 'visible': False}}})
            assert saved['ok'], saved
            profile = Path(td) / 'profiles/demo'
            files = ['layout-splitscreen.json', 'splitscreen.css']
            expected = {name: (profile / 'overlay' / name).read_bytes() for name in files}
            archive = profile_io.export_profile('demo', {'profile_dir': str(profile)}, False,
                                                str(Path(td) / 'profile.zip'))
            dest = Path(td) / 'destination'
            dest.mkdir()
            profile_io.import_profile(archive, {'profiles_root': str(dest / 'profiles'),
                                                'runtime_root': str(dest / 'runtime')})
            assert all((dest / 'profiles/demo/overlay' / name).read_bytes() == value
                       for name, value in expected.items())
            sources = {'overlay': str(profile / 'overlay'), 'backups': str(Path(td) / 'backups')}
            backup = backup_admin.create_backup('Split', sources, 'demo')
            for name in files:
                (profile / 'overlay' / name).write_text('changed', encoding='utf-8')
            backup_admin.restore_backup(backup, sources)
            assert all((profile / 'overlay' / name).read_bytes() == value
                       for name, value in expected.items())
        finally:
            m._env_base, m._runtime_base_dir = original


def t_split_http_keeps_shared_hud_data_and_falls_back_until_first_save():
    import urllib.request
    with tempfile.TemporaryDirectory() as td:
        overlay = Path(td)
        hud_css = '#clock{color:gold}'
        split_css = '#split-left{background:blue}'
        (overlay / 'hud.css').write_text(hud_css, encoding='utf-8')
        (overlay / 'splitscreen.css').write_text(split_css, encoding='utf-8')
        base_handler = gate.m.make_handler
        def handler(*args, **kwargs):
            kwargs.update(overlay_dir=td, hud_path=str(ROOT / 'src/obs/hud.html'),
                          splitscreen_path=str(ROOT / 'src/obs/splitscreen.html'))
            return base_handler(*args, **kwargs)
        with patch.object(gate.m, 'make_handler', side_effect=handler):
            server = gate._serve()
        port = server.server_address[1]
        def get(path):
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
                return response.read().decode('utf-8')
        try:
            html = get('/splitscreen/hud')
            assert '/splitscreen/hud/override.css' in html
            assert "fetch('/hud/data'" in html or 'fetch("/hud/data"' in html
            assert '/timer/data' in html
            assert get('/splitscreen/hud/override.css') == hud_css
            assert get('/splitscreen/override.css') == split_css
            (overlay / 'layout-splitscreen.json').write_text('{}', encoding='utf-8')
            assert get('/splitscreen/hud/override.css') == split_css
            (overlay / 'hud.css').write_text('changed HUD', encoding='utf-8')
            assert get('/splitscreen/hud/override.css') == split_css
            assert get('/hud/override.css') == 'changed HUD'
        finally:
            server.shutdown()
            server.server_close()


def t_split_api_failure_shapes_and_files_remain_unchanged():
    import test_ui_server as ui
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        context = ui._ctx()
        context.update(overlay_layout_write=m.overlay_layout_write_data,
                       overlay_layout_read=m.overlay_layout_read_data,
                       overlay_slots=m.overlay_slots_data,
                       overlay_apply=lambda page: m.overlay_apply_data(page,
                           refresh=lambda **kw: ([], 'OBS disconnected')))
        server, port = ui._serve(context)
        try:
            with patch.object(m, '_profile_is_solo', return_value=False):
                status, body = ui._post_json(port, '/api/overlay/layout', {
                    'page': 'splitscreen', 'layout': {'page': 'hud'}})
                assert status == 400 and json.loads(body) == {
                    'ok': False, 'error': 'layout page mismatch'}
                status, body = ui._post_json(port, '/api/overlay/apply', {'page': 'splitscreen'})
                assert status == 400 and json.loads(body)['error'] == 'OBS disconnected'
            with patch.object(m, '_profile_is_solo', return_value=True):
                status, body = ui._post_json(port, '/api/overlay/layout', {
                    'page': 'splitscreen', 'layout': {'slots': {}}})
                assert status == 400 and json.loads(body) == {
                    'ok': False, 'error': 'no active profile or invalid page'}
            assert not (Path(td) / 'profiles/demo/overlay').exists()
        finally:
            server.shutdown()
            server.server_close()
            m._env_base, m._runtime_base_dir = original


def t_new_cover_uses_current_customized_split_feed_transform():
    import test_obsws as ws
    transform = obs_ws.pov_scene_item_transform({'left': 80, 'top': 100, 'width': 1200, 'height': 675})
    session = ws._FakeSession({
        'GetInputList': {'inputs': [{'inputName': 'Feed A'}]},
        'GetSceneItemList': {'sceneItems': [
            {'sourceName': 'Feed A', 'sceneItemId': 3, 'sceneItemIndex': 1}]},
        'GetSceneItemTransform': {'sceneItemTransform': {**transform, 'sourceWidth': 1920, 'width': 1200}},
        'CreateInput': {'sceneItemId': 11}})
    report, note = obs_ws.ensure_cover_items('Feed A', 'Feed A Cover', 'fixture.jpg',
                                             scenes=('Splitscreen',), session=session)
    assert report == {'Splitscreen': 'created'} and not note
    writes = [data for kind, data in session.sent if kind == 'SetSceneItemTransform']
    assert writes == [{'sceneName': 'Splitscreen', 'sceneItemId': 11, 'sceneItemTransform': transform}]
    assert not any(kind in ('SetCurrentProgramScene', 'SetInputMute', 'SetSceneItemEnabled')
                   for kind, _ in session.sent)


def t_split_internal_profile_gates_are_independent():
    html = (ROOT / 'src/obs/splitscreen.html').read_text(encoding='utf-8')
    with patch.object(m, '_profile_is_solo', return_value=True):
        assert m._overlay_base_html('splitscreen') == '', 'Solo has no Split builder base'
        with patch.object(m, '_overlay_base_html', return_value=html):
            result = m.overlay_slots_data('splitscreen')
            assert result == {'ok': False, 'error': 'invalid page'}, result
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        overlay = Path(td) / 'profiles/demo/overlay'
        overlay.mkdir()
        (overlay / 'splitscreen.css').write_text('#split-feed-a{left:80px}', encoding='utf-8')
        calls = []
        try:
            with patch.object(m, '_profile_is_solo', return_value=False):
                result = m._sync_scene_layouts(page='splitscreen', solo=True,
                    set_transform=lambda *args: (calls.append(args) or (True, '')),
                    set_enabled=lambda *args: (calls.append(args) or (True, '')))
            assert result['ok'] and not calls, 'Explicit Solo sync must refuse Split video geometry'
        finally:
            m._env_base, m._runtime_base_dir = original


def t_legacy_label_css_does_not_reset_existing_obs_video_bounds():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        overlay = Path(td) / 'profiles/demo/overlay'
        overlay.mkdir()
        (overlay / 'splitscreen.css').write_text('#split-left{color:gold}', encoding='utf-8')
        calls = []
        try:
            with patch.object(m, '_profile_is_solo', return_value=False):
                result = m._sync_scene_layouts(page='splitscreen',
                    set_transform=lambda *args: (calls.append(args) or (True, '')),
                    set_enabled=lambda *args: (calls.append(args) or (True, '')))
            assert result['ok'] and not calls, 'Label-only legacy CSS must never reset live video geometry'
        finally:
            m._env_base, m._runtime_base_dir = original


def t_split_base_feed_boxes_are_complete_so_a_move_only_edit_keeps_its_size():
    # A saved splitscreen.css that only moves a feed must still yield a full box:
    # obs_ws.pov_scene_item_transform needs left/top/width/height.
    import overlay_build as ob
    with patch.object(m, '_profile_is_solo', return_value=False):
        base = ob.base_style(m._overlay_base_html('splitscreen'))
    for sid, box in ob.scene_boxes('splitscreen', base, '').items():
        assert set(box) == {'left', 'top', 'width', 'height'}, (sid, box)
    moved = ob.scene_boxes('splitscreen', base, '#split-feed-a{left:80px;top:100px}')
    assert moved['split-feed-a'] == {'left': 80, 'top': 100, 'width': 960, 'height': 540}, moved
    assert moved['split-feed-b'] == {'left': 960, 'top': 270, 'width': 960, 'height': 540}, moved


def t_split_apply_of_a_move_only_feed_succeeds_with_the_default_size():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        overlay = Path(td) / 'profiles/demo/overlay'
        overlay.mkdir()
        (overlay / 'splitscreen.css').write_text('#split-feed-a{left:80px;top:100px}', encoding='utf-8')
        transforms = []
        try:
            with patch.object(m, '_profile_is_solo', return_value=False), patch.object(
                    obs_ws, 'get_scene_items', return_value=({}, '')):
                result = m.overlay_apply_data('splitscreen',
                    set_transform=lambda *args: (transforms.append(args) or (True, '')),
                    set_enabled=lambda *args: (True, ''),
                    refresh=lambda **kw: (['Splitscreen Labels', 'Splitscreen HUD Overlay'], ''))
            assert result['ok'], result
            assert [(a[0], a[1]) for a in transforms] == [('Splitscreen', 'Feed A')], transforms
            t = transforms[0][2]
            assert (t['positionX'], t['positionY'], t['boundsWidth'], t['boundsHeight']) == (80, 100, 960, 540), t
        finally:
            m._env_base, m._runtime_base_dir = original


def t_split_apply_does_not_refresh_pages_when_the_geometry_sync_fails():
    # Apply must not leave OBS half-updated: pages reloaded with the new design while
    # the feed geometry stayed old. A failed sync reports and refreshes nothing.
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        overlay = Path(td) / 'profiles/demo/overlay'
        overlay.mkdir()
        (overlay / 'splitscreen.css').write_text(
            '#split-feed-a{left:80px;top:100px;width:1200px;height:675px}', encoding='utf-8')
        pressed = []

        def refresh(**kw):
            if kw.get('press', True):
                pressed.append(kw)
            return ['Splitscreen Labels', 'Splitscreen HUD Overlay'], ''
        try:
            with patch.object(m, '_profile_is_solo', return_value=False):
                result = m.overlay_apply_data('splitscreen',
                    set_transform=lambda *args: (False, 'transform rejected'),
                    set_enabled=lambda *args: (True, ''), refresh=refresh)
            assert not result['ok'] and 'transform rejected' in result['error'], result
            assert pressed == [], 'A failed geometry sync must not reload the Split pages'
            with patch.object(m, '_profile_is_solo', return_value=False), patch.object(
                    obs_ws, 'get_scene_items', return_value=({}, '')):
                result = m.overlay_apply_data('splitscreen',
                    set_transform=lambda *args: (True, ''),
                    set_enabled=lambda *args: (True, ''), refresh=refresh)
            assert result['ok'] and len(pressed) == 1, (result, pressed)
        finally:
            m._env_base, m._runtime_base_dir = original


def t_relay_browser_sources_stay_alive_across_scene_cuts():
    # restart_when_active reloads a browser page whenever its scene goes live: the page
    # blanks on air and rebuilds its state. Every relay page polls the relay for its data
    # and relay/event start reloads them all (refreshnocache), so no relay-served source
    # needs it (#956). Stint and Split each carry their own HUD browser since #940.
    for path in sorted((ROOT / 'src/obs').glob('GT_Racing_*.json')):
        collection = json.loads(path.read_text(encoding='utf-8'))
        relay = {s['name']: s['settings'] for s in collection['sources']
                 if s.get('id') == 'browser_source'
                 and '127.0.0.1:8088' in s.get('settings', {}).get('url', '')}
        assert 'HUD Overlay' in relay and 'Intermission Chat' in relay, (path.name, sorted(relay))
        reloading = sorted(n for n, st in relay.items() if st.get('restart_when_active') is not False)
        assert reloading == [], (path.name, reloading)


if __name__ == '__main__':
    for name in sorted(globals()):
        if name.startswith('t_'):
            globals()[name]()
            print('PASS', name)

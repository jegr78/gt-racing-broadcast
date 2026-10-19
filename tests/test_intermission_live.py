"""Behavioral contracts for Intermission live overrides, independent of real OBS."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import intermission_live as live


class Obs:
    def __init__(self):
        self.items = {'Intermission Chat': True, 'Solo Webcam': False, 'Solo Capture': False}
        self.calls = []
        self.note = ''

    def get_scene_items(self, scene):
        assert scene == 'Intermission', scene
        items = {} if self.note else dict(self.items)
        time.sleep(.002)
        return items, self.note

    def set_scene_item_enabled(self, scene, source, enabled):
        assert scene == 'Intermission', scene
        self.calls.append((scene, source, enabled))
        if source not in self.items:
            return False, 'Scene item missing'
        self.items[source] = enabled
        return True, ''


def control(obs, solo=True, path=None, defaults=None):
    return live.IntermissionControl(lambda: obs, solo,
        lambda: defaults or {'chat': True, 'webcam': False, 'capture': False}, path)


def t_repeat_video_toggle_reads_actual_item_and_never_touches_program():
    obs = Obs()
    ctl = control(obs)
    for wanted in (True, False, True):
        value, status = ctl.apply('toggle', 'webcam')
        assert status == 200 and value['items']['webcam'] is wanted, value
    obs.items['Solo Webcam'] = False
    assert ctl.state()[0]['items']['webcam'] is False
    assert obs.calls == [('Intermission', 'Solo Webcam', x) for x in (True, False, True)]


def t_concurrent_toggles_are_serialized():
    obs = Obs()
    ctl = control(obs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: ctl.apply('toggle', 'webcam'), range(2)))
    assert all(status == 200 for _, status in answers), answers
    assert obs.items['Solo Webcam'] is False
    assert obs.calls == [('Intermission', 'Solo Webcam', True), ('Intermission', 'Solo Webcam', False)]


def t_chat_override_survives_restart_and_restore_reads_current_defaults():
    obs = Obs()
    defaults = {'chat': True, 'webcam': False, 'capture': False}
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / 'intermission-live.json')
        ctl = control(obs, path=path, defaults=defaults)
        assert ctl.apply('hide', 'chat')[1] == 200
        assert obs.calls == [], 'Hiding chat must leave the frame browser item enabled'
        ctl = control(obs, path=path, defaults=defaults)
        assert ctl.state()[0]['items']['chat'] is False
        defaults['webcam'] = True
        value, status = ctl.apply('restore')
        assert status == 200 and value['items']['chat'] is True, value
        assert value['items']['webcam'] is True
        assert control(obs, path=path, defaults=defaults).state()[0]['items']['chat'] is True


def t_video_override_survives_automatic_refresh_without_revealing_hidden_camera():
    import json
    import test_racecast as cli
    m = cli.m
    obs = Obs()
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            runtime = Path(m._runtime_dir())
            runtime.mkdir(parents=True, exist_ok=True)
            path = runtime / 'intermission-live.json'
            ctl = control(obs, path=str(path))
            assert ctl.apply('show', 'webcam')[1] == 200
            assert json.loads(path.read_text(encoding='utf-8'))['video']['webcam'] is True
            overlay = Path(td) / 'profiles/demo/overlay'
            overlay.mkdir()
            (overlay / 'intermission.css').write_text('#intermission-webcam{display:none}', encoding='utf-8')
            calls = []
            m._sync_scene_layouts(solo=True, set_transform=lambda *a: (True, ''),
                set_enabled=lambda *a: (calls.append(a) or (True, '')))
            assert not calls, calls
            assert ctl.apply('hide', 'webcam')[1] == 200
            (overlay / 'intermission.css').write_text('#intermission-webcam{display:block}', encoding='utf-8')
            m._sync_scene_layouts(solo=True, set_transform=lambda *a: (True, ''),
                set_enabled=lambda *a: (calls.append(a) or (True, '')))
            assert calls == [('Intermission', 'Solo Webcam', False)], calls
            assert ctl.apply('restore')[1] == 200
            assert json.loads(path.read_text(encoding='utf-8'))['video'] == {}
        finally:
            m._env_base, m._runtime_base_dir = original


def t_chat_show_overrides_hidden_design_and_manually_hidden_browser():
    obs = Obs()
    obs.items['Intermission Chat'] = False
    ctl = control(obs, defaults={'chat': False, 'webcam': False, 'capture': False})
    value, status = ctl.apply('toggle', 'chat')
    assert status == 200 and value['items']['chat'] is True, value
    assert obs.calls == [('Intermission', 'Intermission Chat', True)]
    value, status = ctl.apply('toggle', 'chat')
    assert status == 200 and value['items']['chat'] is False, value
    assert obs.items['Intermission Chat'] is True


def t_missing_item_stays_unknown_and_cannot_be_toggled():
    obs = Obs()
    del obs.items['Solo Webcam']
    ctl = control(obs)
    value, status = ctl.state()
    assert status == 200 and value['items']['webcam'] is None, value
    assert value['items']['chat'] is True
    value, status = ctl.apply('toggle', 'webcam')
    assert status == 503 and value['ok'] is False and value['error'], value
    assert obs.calls == []


def t_disconnected_obs_has_no_enabled_guess_or_mutation():
    obs = Obs()
    obs.note = 'OBS disconnected'
    ctl = control(obs)
    value, status = ctl.state()
    assert status == 503 and value['ok'] is False and value['error']
    assert all(x is None for x in value['items'].values()), value
    value, status = ctl.apply('show', 'chat')
    assert status == 503 and value['error'], value
    assert obs.calls == []


def t_failed_override_write_keeps_previous_state_and_cleans_temporary_file():
    obs = Obs()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'live.json'
        ctl = control(obs, path=str(path))
        with patch.object(live.os, 'replace', side_effect=OSError('disk full')):
            value, status = ctl.apply('hide', 'chat')
        assert status == 503 and value['ok'] is False and value['error'], value
        assert ctl.state()[0]['items']['chat'] is True
        assert list(Path(td).iterdir()) == []
        assert obs.calls == []


def t_corrupt_override_is_hidden_until_explicit_restore():
    obs = Obs()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'live.json'
        path.write_text('{invalid', encoding='utf-8')
        ctl = control(obs, path=str(path))
        state, status = ctl.state()
        assert status == 200 and state['items']['chat'] is False and state['notes']['chat'], state
        assert ctl.apply('restore')[1] == 200
        assert control(obs, path=str(path)).state()[0]['items']['chat'] is True


def t_profile_and_action_allowlists_reject_before_any_write():
    obs = Obs()
    ctl = control(obs, solo=False)
    assert set(ctl.state()[0]['items']) == {'chat'}
    for verb, key in [('show', 'webcam'), ('toggle', 'capture'), ('show', '../chat'),
                      ('unmute', 'chat'), ('show', []), ('show', {})]:
        value, status = ctl.apply(verb, key)
        assert status == 400 and value['ok'] is False and value['error'], value
    assert obs.calls == []


def t_templates_are_profile_aware_and_only_patch_scene_slots():
    assert [x['id'] for x in live.starter_layouts(False)] == ['background-chat', 'break-goals']
    solo = live.starter_layouts(True)
    assert len(solo) == 4
    assert all(set(x['slots']) <= {'ichat', 'intermission-webcam', 'intermission-capture'} for x in solo)
    assert solo[-1]['slots']['intermission-webcam']['visible'] is False
    assert solo[-1]['slots']['ichat']['visible'] is False


def t_companion_export_offers_only_supported_intermission_controls():
    import json
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / 'src/companion/racecast-buttons.companionconfig').read_text(encoding='utf-8'))
    def urls(config):
        return [action.get('options', {}).get('url', {}).get('value', '')
                for page in config['pages'].values() for row in page['controls'].values()
                for button in row.values() for step in button.get('steps', {}).values()
                for actions in step.get('action_sets', {}).values() for action in actions]
    all_urls = urls(data)
    assert sum('/obs/intermission/' in u for u in all_urls) == 4
    endurance = live.companion_config(data, False)
    filtered = urls(endurance)
    assert len(all_urls) - len(filtered) == 2
    assert not any('/obs/intermission/toggle/webcam' in u or '/obs/intermission/toggle/capture' in u for u in filtered)
    assert urls(live.companion_config(data, True)) == all_urls
    assert urls(data) == all_urls, 'Export must not mutate the bundled source'


def t_apply_saved_design_explicitly_restores_live_defaults():
    import test_racecast as cli
    m = cli.m
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        calls = []
        try:
            result = m.overlay_apply_data('intermission',
                refresh=lambda **kw: (['Intermission Chat'], ''),
                restore=lambda: calls.append('restore') or {'ok': True})
            assert result['ok'] and calls == ['restore'], result
        finally:
            m._env_base, m._runtime_base_dir = original


def t_visibility_only_video_design_is_an_explicit_default():
    import test_racecast as cli
    m = cli.m
    html = (Path(__file__).resolve().parents[1] / 'src/obs/intermission.html').read_text(encoding='utf-8')
    css = m.ob.compile_overlay_css({'slots': {'intermission-webcam': {'visible': True}}}, m.ob.extract_slots(html))
    assert 'display: block' in css, css
    import json
    import test_solo_obs
    root = Path(__file__).resolve().parents[1]
    collection = json.loads((root / 'src/obs/GT_Racing_Solo_Commentary.json').read_text(encoding='utf-8'))
    test_solo_obs.sa.bake_scene_layout(collection, 'intermission', html, css)
    scene = next(s for s in collection['sources'] if s['name'] == 'Intermission')
    camera = next(i for i in scene['settings']['items'] if i['name'] == 'Solo Webcam')
    assert camera['visible'] is True, camera
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        overlay = Path(td) / 'profiles/demo/overlay'
        overlay.mkdir()
        (overlay / 'intermission.css').write_text(css, encoding='utf-8')
        calls = []
        try:
            m._sync_scene_layouts(solo=True, explicit=True,
                set_transform=lambda *a: (True, ''),
                set_enabled=lambda *a: (calls.append(a) or (True, '')))
            assert ('Intermission', 'Solo Webcam', True) in calls, calls
        finally:
            m._env_base, m._runtime_base_dir = original


def t_live_http_routes_and_director_gate():
    import test_console_gate as gate
    obs = Obs()
    srv = gate._serve(solo=True)
    port = srv.server_address[1]
    try:
        with patch.object(gate.m, '_obs_ws', obs):
            code, raw = gate._get(port, '/obs/intermission/data')
            assert code == 200, (code, raw)
            import json
            assert json.loads(raw)['items']['webcam'] is False
            code, raw = gate._get(port, '/obs/intermission/toggle/webcam')
            assert code == 200 and json.loads(raw)['items']['webcam'] is True, (code, raw)
            for role in ('alice', 'dave'):
                code, _ = gate._post(port, '/console/obs/intermission', gate._tok(role),
                                     body={'action': 'hide', 'key': 'webcam'})
                assert code == 403
            assert obs.items['Solo Webcam'] is True
            code, raw = gate._post(port, '/console/obs/intermission', gate._tok('bob'),
                                   body={'action': 'hide', 'key': 'webcam'})
            assert code == 200 and json.loads(raw)['items']['webcam'] is False, (code, raw)
            code, raw = gate._post(port, '/obs/intermission', body={'action': 'show', 'key': 'unknown'})
            assert code == 400 and json.loads(raw)['ok'] is False and json.loads(raw)['error']
            code, raw = gate._get(port, '/intermission/data')
            assert code == 200 and set(json.loads(raw)) == {'chatOverride', 'video'}, (code, raw)
    finally:
        srv.shutdown()
        srv.server_close()


def t_foreign_browser_controls_cannot_change_obs_or_persisted_overrides():
    import json
    import urllib.request
    import urllib.error
    import test_console_gate as gate
    obs = Obs()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'live.json'
        original = gate.m.make_handler
        def handler(*args, **kwargs):
            return original(*args, **kwargs, intermission_runtime_path=str(path))
        with patch.object(gate.m, 'make_handler', side_effect=handler):
            srv = gate._serve(solo=True)
        port = srv.server_address[1]
        try:
            with patch.object(gate.m, '_obs_ws', obs):
                assert gate._get(port, '/obs/intermission/hide/chat')[0] == 200
                before = path.read_bytes()
                for headers in ({'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                                {'Origin': 'https://foreign.example'}, {'Origin': 'null'},
                                {'Referer': 'https://foreign.example/page'}):
                    for method in ('GET', 'POST'):
                        url = f'http://127.0.0.1:{port}/obs/intermission'
                        body = None
                        if method == 'GET':
                            url += '/show/webcam'
                        else:
                            body = json.dumps({'action': 'show', 'key': 'webcam'}).encode()
                        req = urllib.request.Request(url, data=body, method=method,
                            headers=dict(headers, **{'Content-Type': 'application/json'}))
                        try:
                            with urllib.request.urlopen(req, timeout=5) as response:
                                status, raw = response.status, response.read()
                        except urllib.error.HTTPError as exc:
                            status, raw = exc.code, exc.read()
                        payload = json.loads(raw)
                        assert status == 403 and payload['ok'] is False and payload['error'], (method, headers, status, payload)
                        assert obs.calls == [] and obs.items['Solo Webcam'] is False
                        assert path.read_bytes() == before
                code, raw = gate._get(port, '/obs/intermission/show/webcam', headers={
                    'Origin': f'http://127.0.0.1:{port}', 'Sec-Fetch-Site': 'same-origin'})
                assert code == 200 and json.loads(raw)['items']['webcam'] is True, raw
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == '__main__':
    for name in sorted(globals()):
        if name.startswith('t_'):
            globals()[name]()
            print('PASS', name)

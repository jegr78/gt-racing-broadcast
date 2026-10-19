#!/usr/bin/env python3
"""Exercise shipped Director/overlay pages and optional isolated Companion buttons.

Run in the Playwright environment. --shots refreshes changed wiki/slide screenshots.
--companion URL imports only into an explicitly supplied disposable Companion.
All OBS reads and writes use the recording fake; no device or real OBS is opened.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_console_gate as gate  # noqa: E402
import test_intermission_live as unit  # noqa: E402


class Obs(unit.Obs):
    def __init__(self, solo):
        super().__init__()
        self.solo = solo

    def read_obs_state(self, sources, inputs):
        return {'scene': 'Program' if self.solo else 'Stint', 'sources': [
            {'scene': scene, 'source': source, 'enabled': self.items.get(source, False)}
            for scene, source in sources],
            'audio': [{'input': name, 'muted': False, 'volumeDb': 0} for name in inputs],
            'stream': {'active': False}}, ''

    def get_program_screenshot(self, **kwargs):
        return (ROOT / 'src/assets/preview-bg.jpg').read_bytes(), ''


def serve(directory, solo):
    m = gate.m
    source = gate._FakeSource(['https://youtu.be/a', 'https://youtu.be/b'],
                             [('https://youtu.be/a', 'Alice', '1', 2),
                              ('https://youtu.be/b', 'Bob', '2', 3)])
    relay = m.Relay(source, [53001, 53002], directory, solo=solo)
    handler = m.make_handler(relay,
        panel_path=str(ROOT / 'src/director/director-panel.html'),
        intermission_path=str(ROOT / 'src/obs/intermission.html'),
        overlay_dir=directory, intermission_runtime_path=str(Path(directory) / 'live.json'),
        chat_store=m.ChatStore(str(Path(directory) / 'chat.json')),
        console_secret=gate.SECRET,
        crew_source=gate._Crew([('Bob', True, False)]))
    server = m.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def shot(page, name):
    path = ROOT / 'src/docs/wiki/images' / name
    page.screenshot(path=str(path))
    copy = ROOT / 'src/docs/slides/assets/img' / name
    if copy.exists():
        shutil.copy2(path, copy)


def hold_one_status(held):
    def handle(route):
        if not held:
            held.append(route)
        else:
            route.continue_()
    return handle


def companion_test(pw, url, relay_port, obs, directory, shots):
    data = json.loads((ROOT / 'src/companion/racecast-buttons.companionconfig').read_text(encoding='utf-8'))
    for instance in data['instances'].values():
        if instance['moduleId'] == 'obs-studio':
            instance['enabled'] = False
            instance['config'].update(host='127.0.0.1', port=9, password='')
    for page in data['pages'].values():
        for row in page['controls'].values():
            for button in row.values():
                for step in button.get('steps', {}).values():
                    for actions in step.get('action_sets', {}).values():
                        for action in actions:
                            value = action.get('options', {}).get('url', {})
                            if isinstance(value.get('value'), str):
                                value['value'] = value['value'].replace('127.0.0.1:8088', '127.0.0.1:' + str(relay_port))
    config = Path(directory) / 'buttons.companionconfig'
    config.write_text(json.dumps(data, indent=1), encoding='utf-8')
    ctx = pw.chromium.launch_persistent_context(str(Path(directory) / 'browser'),
        headless=True, viewport={'width': 1280, 'height': 720})
    try:
        page = ctx.pages[0]
        page.goto(url + '/import-export')
        page.locator('input[type=file]').first.wait_for(state='attached')
        for _ in range(3):
            page.keyboard.press('Escape')
        page.locator('input[type=file]').first.set_input_files(str(config))
        page.get_by_role('button', name='Import Preserving Unselected').click()
        page.wait_for_timeout(1500)
        page.goto(url + '/tablet?pages=1')
        page.locator('.button-control.clickable').nth(31).wait_for()
        page.wait_for_timeout(1000)
        obs.items['Solo Webcam'] = False
        page.locator('.button-control.clickable').nth(29).click()
        page.wait_for_timeout(500)
        assert obs.items['Solo Webcam'] is True, 'Actual Companion webcam button did not fire'
        page.locator('.button-control.clickable').nth(29).click()
        page.wait_for_timeout(300)
        assert obs.items['Solo Webcam'] is False
        page.locator('.button-control.clickable').nth(30).click()
        page.wait_for_timeout(300)
        assert obs.items['Solo Capture'] is True
        page.locator('.button-control.clickable').nth(28).click()
        page.wait_for_timeout(300)
        status, raw = gate._get(relay_port, '/obs/intermission/data')
        assert status == 200 and json.loads(raw)['items']['chat'] is True
        page.locator('.button-control.clickable').nth(31).click()
        page.wait_for_timeout(300)
        assert obs.items['Solo Capture'] is False
        if shots:
            page.screenshot(path=str(ROOT / 'src/docs/wiki/images/companion-page1-show-control.png'),
                            clip={'x': 0, 'y': 54, 'width': 1280, 'height': 632})
            slide = ROOT / 'src/docs/slides/assets/img/companion-page1-show-control.png'
            if slide.exists():
                shutil.copy2(ROOT / 'src/docs/wiki/images/companion-page1-show-control.png', slide)
    finally:
        ctx.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shots', action='store_true')
    parser.add_argument('--companion')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    errors = []
    with tempfile.TemporaryDirectory(prefix='intermission-browser-') as directory:
        Path(directory, 'intermission.css').write_text('#ichat{display:none!important}', encoding='utf-8')
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for solo in (False, True):
                    obs = Obs(solo)
                    server = serve(directory, solo)
                    port = server.server_address[1]
                    base = 'http://127.0.0.1:' + str(port)
                    try:
                        with patch.object(gate.m, '_obs_ws', obs):
                            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                            page.on('pageerror', lambda error: errors.append(error.stack))
                            page.goto(base + '/panel')
                            page.click('.navbtn[data-area=intermission]')
                            page.locator('[data-intermission=chat]').wait_for()
                            assert page.locator('[data-intermission]').count() == (3 if solo else 1)
                            page.click('[data-intermission=chat]')
                            page.wait_for_function("document.querySelector('[data-intermission=chat]').textContent.includes('shown')")
                            overlay = browser.new_page(viewport={'width': 1920, 'height': 1080})
                            overlay.on('pageerror', lambda error: errors.append(error.stack))
                            overlay.goto(base + '/intermission')
                            overlay.wait_for_function("document.body.dataset.liveReady === 'true'")
                            assert overlay.locator('#ichat').is_visible(), 'Live show must override hidden design CSS'
                            page.click('[data-intermission=chat]')
                            overlay.wait_for_function("getComputedStyle(document.getElementById('ichat')).display === 'none'")
                            overlay.reload()
                            overlay.wait_for_function("document.body.dataset.liveReady === 'true'")
                            assert not overlay.locator('#ichat').is_visible()
                            if solo:
                                page.click('[data-intermission=webcam]')
                                page.wait_for_function("document.querySelector('[data-intermission=webcam]').textContent.includes('shown')")
                                assert obs.items['Solo Webcam'] is True
                                page.click('[data-intermission=webcam]')
                                page.wait_for_function("document.querySelector('[data-intermission=webcam]').textContent.includes('hidden')")
                                assert obs.items['Solo Webcam'] is False
                                page.wait_for_function('!intermissionPending')
                                status, raw = gate._get(port, '/obs/intermission/data')
                                assert status == 200
                                held = []
                                page.route('**/obs/intermission/data', hold_one_status(held))
                                page.evaluate('void intermissionPoll()')
                                deadline = time.monotonic() + 3
                                while not held and time.monotonic() < deadline:
                                    page.wait_for_timeout(20)
                                assert held, 'The stale-state scenario needs an in-flight snapshot'
                                page.click('[data-intermission=webcam]')
                                page.wait_for_function("document.querySelector('[data-intermission=webcam]').textContent.includes('shown')")
                                held[0].fulfill(status=200, content_type='application/json', body=raw)
                                page.wait_for_timeout(100)
                                assert 'shown' in page.locator('[data-intermission=webcam]').inner_text(), 'Old poll overwrote action result'
                                page.unroute('**/obs/intermission/data')
                                page.click('[data-intermission=webcam]')
                                page.wait_for_function("document.querySelector('[data-intermission=webcam]').textContent.includes('hidden')")
                                obs.items['Solo Webcam'] = True
                                page.wait_for_function("document.querySelector('[data-intermission=webcam]').textContent.includes('shown')")
                                overlay.wait_for_function("getComputedStyle(document.getElementById('intermission-webcam')).display === 'block'")
                                if args.shots:
                                    shot(page, 'director-intermission.png')
                                del obs.items['Solo Webcam']
                                page.wait_for_function("document.querySelector('[data-intermission=webcam]').disabled")
                                obs.items['Solo Webcam'] = False
                                if args.companion:
                                    companion_test(pw, args.companion.rstrip('/'), port, obs, directory, args.shots)
                            elif args.shots:
                                page.click('.navbtn[data-area=handover]')
                                shot(page, 'director-panel.png')
                            obs.note = 'OBS disconnected'
                            page.wait_for_function("document.querySelector('[data-intermission=chat]').textContent.includes('unknown')")
                            assert page.locator('#intermissionRestore').is_disabled()
                            assert all(call[0] == 'Intermission' for call in obs.calls), obs.calls
                            overlay.close()
                            page.close()
                    finally:
                        server.shutdown()
                        server.server_close()
                        Path(directory, 'live.json').unlink(missing_ok=True)
            finally:
                browser.close()
    assert not errors, errors
    print(json.dumps({'ok': True, 'profiles': ['endurance', 'solo'], 'browserErrors': errors}))


if __name__ == '__main__':
    main()

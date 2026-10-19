#!/usr/bin/env python3
"""Exercise the real scene builder UI/API with synthetic profiles and no OBS.

Run with the Playwright Python environment. --shots refreshes builder wiki images.
Only unrelated machine/hardware panels use the existing UI-server fixture.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_racecast as cli  # noqa: E402
import test_ui_server as ui  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shots', action='store_true')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    m = cli.m
    with tempfile.TemporaryDirectory(prefix='scene-builder-') as td:
        original = cli._mk_active_profile(td)
        profile = Path(td) / 'profiles/demo/profile.env'
        profile.write_text('NAME=Demo Racing\nKIND=solo\nTEMPLATE=pov\n', encoding="utf-8")
        calls = []
        ctx = ui._ctx()
        ctx.update(version='dev build',
            status=lambda: {'relay': {'alive': False}, 'streams': [], 'companion': {'running': False}},
            overlay_asset_serve=m.overlay_asset_serve,
            overlay_font_serve=m.overlay_font_serve,
            update_check=lambda force=False: {'ok': True, 'note': 'development build'},
            profiles=lambda: {'ok': True, 'active': 'demo', 'profiles': [
                {'name': 'demo', 'display': 'Demo Racing', 'kind': 'solo', 'template': 'pov'}]},
            overlay_slots=m.overlay_slots_data,
            overlay_layout_read=m.overlay_layout_read_data,
            overlay_layout_write=m.overlay_layout_write_data,
            overlay_fonts=m.overlay_fonts_list_data,
            overlay_bg=m.overlay_bg_path,
            overlay_apply=lambda page: m.overlay_apply_data(page,
                set_transform=lambda *a: (calls.append(('transform', a)) or (True, '')),
                set_enabled=lambda *a: (calls.append(('visibility', a)) or (True, '')),
                refresh=lambda **kw: (['Intermission Chat'], '')))
        server, port = ui._serve(ctx)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={'width': 1600, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(error.stack))
                page.goto(f'http://127.0.0.1:{port}/#profile')
                page.wait_for_function("() => ovState.layout && ovState.slots.length > 0")
                page.evaluate('openBuilderModal()')
                page.wait_for_timeout(150)
                if args.shots:
                    page.locator('#ov-modal .ovmodal-card').screenshot(
                        path=str(ROOT / 'src/docs/wiki/images/cc-overlay-builder.png'))
                    shutil.copy2(ROOT / 'src/docs/wiki/images/cc-overlay-builder.png',
                                 ROOT / 'src/docs/slides/assets/img/cc-overlay-builder.png')
                page.select_option('#ov-scene', 'intermission')
                page.wait_for_function("() => ovState.page === 'intermission' && ovState.slots.length === 3")
                assert page.evaluate("ovState.shadow.getElementById('ichat').getBoundingClientRect().width") > 100
                assert page.evaluate("ovState.shadow.getElementById('ichat-log').textContent.includes('RaceFan')")
                page.evaluate("ovPreviewText('ichat', '')")
                assert page.evaluate("ovState.shadow.getElementById('ichat-log').textContent") == ''
                page.evaluate('ovPreviewReset()')
                if args.shots:
                    page.locator('#ov-modal .ovmodal-card').screenshot(
                        path=str(ROOT / 'src/docs/wiki/images/cc-intermission-builder.png'))
                page.evaluate("ovSelect('ichat')")
                for prop, value in [('left', '120'), ('top', '100'), ('width', '480'), ('height', '600')]:
                    page.evaluate("([prop,value]) => { const input=ovState.fields[prop]; input.value=value; input.dispatchEvent(new Event('input')); input.dispatchEvent(new Event('change')); }", [prop, value])
                assert page.evaluate('ovState.layout.slots.ichat.left') == 120, errors
                page.evaluate('saveOverlay()')
                page.wait_for_function("() => document.getElementById('overlay-save').textContent.includes('Saved')")
                css_path = Path(td) / 'profiles/demo/overlay/intermission.css'
                css = css_path.read_text(encoding="utf-8")
                assert 'left: 120px' in css and 'width: 480px' in css
                assert 'rgba(25,40,60' not in css, 'Preview placeholder leaked into saved style'
                page.evaluate('applyOverlay()')
                page.wait_for_timeout(250)
                assert calls and all(call[1][0] == 'Intermission' for call in calls)
                assert ('visibility', ('Intermission', 'Solo Webcam', True)) in calls
                assert ('visibility', ('Intermission', 'Solo Capture', False)) in calls
                page.select_option('#ov-scene', 'hud')
                page.wait_for_function("() => ovState.page === 'hud' && ovState.slots.some(s=>s.id==='clock')")
                assert 'ichat' not in page.evaluate('Object.keys(ovState.layout.slots)')
                page.select_option('#ov-scene', 'intermission')
                page.wait_for_function("() => ovState.page === 'intermission' && ovState.layout.slots.ichat.left===120")
                page.reload()
                page.wait_for_function("() => ovState.page === 'hud' && ovState.slots.length > 0")
                page.evaluate('openBuilderModal()')
                page.select_option('#ov-scene', 'intermission')
                page.wait_for_function("() => ovState.page === 'intermission' && ovState.layout.slots.ichat.left===120")
                # The same UI exposes just chat for an Endurance profile.
                profile.write_text('NAME=Demo Racing\nKIND=endurance\n', encoding="utf-8")
                with patch.object(m, '_profile_is_solo', return_value=False):
                    page.evaluate('loadOverlay()')
                    page.wait_for_function("() => ovState.slots.length === 1")
                    assert page.evaluate('ovState.slots[0].id') == 'ichat'
                    assert not page.evaluate("!!ovState.shadow.getElementById('intermission-webcam')")
                assert not errors, errors
                browser.close()
            print(json.dumps({'ok': True, 'savedScene': 'intermission',
                              'obsMutations': len(calls), 'browserErrors': errors}))
        finally:
            server.shutdown()
            server.server_close()
            m._env_base, m._runtime_base_dir = original


if __name__ == '__main__':
    main()

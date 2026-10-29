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


def check_aspect_resize(page):
    """Use the production controls, pointer handlers, save API and Undo stack."""
    original = page.evaluate('JSON.stringify(ovState.layout)')

    def select(slot):
        page.select_option('#ov-slotpick', slot)

    def number(prop, value):
        field = page.locator('#ov-prop-' + prop)
        field.fill(str(value))
        field.press('Tab')

    def box():
        return page.evaluate('ovBuilderBox(ovState.shadow.getElementById(ovState.sel))')

    def proportional(pair):
        geometry = page.evaluate('ovState.layout.slots[ovState.sel]')
        assert abs(geometry['height'] - geometry['width'] * pair[1] / pair[0]) < 0.0011, geometry

    def drag(direction, dx, dy, shift=False):
        grip = page.locator('#ov-stage').locator('.ov-h-' + direction)
        grip.scroll_into_view_if_needed()
        handle = grip.bounding_box()
        assert handle
        previous = page.evaluate('JSON.stringify(ovState.layout.slots[ovState.sel])')
        if shift:
            page.keyboard.down('Shift')
        try:
            x, y = handle['x'] + handle['width']/2, handle['y'] + handle['height']/2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + dx, y + dy, steps=4)
            page.mouse.up()
        finally:
            if shift:
                page.keyboard.up('Shift')
        assert page.evaluate('JSON.stringify(ovState.layout.slots[ovState.sel])') != previous, 'The actual pointer gesture must resize the selected slot'

    lock = page.locator('#ov-aspect-lock')
    select('webcam')
    before = box()
    assert lock.is_checked()
    assert before['width'] == 336 and before['height'] == 189, before
    number('top', 100)
    number('width', 672)
    assert box()['height'] == 378
    number('height', 270)
    assert box()['width'] == 480
    page.click('#overlay-undo')
    assert box()['width'] == 672 and box()['height'] == 378
    page.click('#overlay-redo')
    assert box()['width'] == 480 and box()['height'] == 270
    for direction, dx, dy in [('e', 25, 0), ('s', 0, 25), ('se', 40, 3)]:
        drag(direction, dx, dy)
        proportional((16, 9))
    # Snap only the driving edge; the paired dimension must retain the ratio.
    page.evaluate('ovGridSnap(true);ovGridSetSize(10);ovZoomSet(0.5)')
    drag('e', 23, 0)
    proportional((16, 9))
    geometry = page.evaluate('ovState.layout.slots.webcam')
    assert geometry['width'] % 10 == 0
    drag('s', 0, 27)
    proportional((16, 9))
    assert page.evaluate('ovState.layout.slots.webcam.height % 10') == 0
    page.evaluate('ovGridSnap(false);ovZoomSet(1.5)')
    drag('s', 0, 18)
    proportional((16, 9))
    page.evaluate("ovZoomSet('fit')")
    # Shift releases a permanent lock for the gesture, then retains that new ratio.
    previous = box()
    drag('e', 40, 0, shift=True)
    changed = box()
    assert abs(changed['height'] - previous['height']) < 0.1 and changed['width'] > previous['width']
    assert lock.is_checked()
    pair = page.evaluate('ovState.layout.slots.webcam.aspectRatio')
    number('width', 700)
    proportional(pair)
    # Presets also repair a distorted box, as one undoable edit.
    lock.uncheck()
    number('width', 400)
    number('height', 400)
    page.select_option('#ov-aspect-preset', '16:9')
    assert box()['width'] == 400 and box()['height'] == 225
    page.click('#overlay-undo')
    assert box()['height'] == 400 and not lock.is_checked()
    page.click('#overlay-redo')
    assert box()['height'] == 225 and lock.is_checked()
    for preset, height in [('4:3', 300), ('1:1', 400)]:
        page.select_option('#ov-aspect-preset', preset)
        assert box()['height'] == height
    page.select_option('#ov-aspect-preset', 'custom')
    custom = page.locator('#ov-aspect-custom')
    old = page.evaluate('JSON.stringify(ovState.layout)')
    custom.fill('0:1'); custom.press('Tab')
    assert page.evaluate('JSON.stringify(ovState.layout)') == old
    assert page.locator('#ov-aspect-error').inner_text()
    custom.fill('3:2'); custom.press('Tab')
    proportional((3, 2))
    number('width', 900)
    assert box()['height'] == 600
    # Unsetting width uses its CSS fallback and also updates height.
    number('width', '')
    assert box()['width'] == 336 and box()['height'] == 224
    pair = page.evaluate('ovState.layout.slots.webcam.aspectRatio')
    for width in [321, 640, 1001, 274, 100.5]:
        number('width', width)
        proportional(pair)
        assert page.evaluate('ovState.layout.slots.webcam.aspectRatio') == pair
    page.evaluate("() => { document.getElementById('overlayCss').value='#webcam{width:777px!important;height:777px!important}';ovCustomCssChanged(); }")
    number('width', 600)
    proportional((3, 2))
    assert page.evaluate('ovState.layout.slots.webcam.height') == 400
    page.evaluate("() => { document.getElementById('overlayCss').value='';ovCustomCssChanged(); }")
    for axis in ['width', 'height']:
        minimum = page.evaluate('axis => ovAspectDimensions(axis,1,[16,9],8)', axis)
        assert minimum['width'] >= 8 and minimum['height'] >= 8
        assert abs(minimum['height'] - minimum['width'] * 9/16) < 0.0011
    # Capture has a deliberate cropped shape; never assume a source is 16:9.
    select('tyres-capture')
    assert lock.is_checked()
    number('width', 490)
    assert box()['height'] == 168
    # Ordinary slots stay free until explicitly locked; Shift is temporary there.
    select('stint')
    assert not lock.is_checked()
    before = box()
    number('width', 300)
    assert box()['height'] == before['height']
    pair = [box()['width'], box()['height']]
    drag('se', 30, 10, shift=True)
    proportional(pair)
    assert not lock.is_checked()
    lock.check()
    pair = page.evaluate('ovState.layout.slots.stint.aspectRatio')
    number('height', 100)
    proportional(pair)
    # Lock metadata survives the actual save/reload flow and is not emitted as CSS.
    select('webcam'); lock.uncheck()
    page.evaluate('saveOverlay()')
    page.wait_for_function("() => document.getElementById('overlay-save').textContent.includes('Saved')")
    page.reload()
    page.wait_for_function("() => ovState.layout && ovState.slots.some(s=>s.id==='webcam')")
    page.evaluate('openBuilderModal()')
    select('webcam')
    assert not lock.is_checked()
    assert page.evaluate('ovState.layout.slots.webcam.aspectRatio') == [3, 2]
    # Restore the original fixture, not a real profile, before the scene checks/shots.
    page.evaluate("raw => { ovState.layout=JSON.parse(raw);ovReloadCanvasFromLayout();ovHistInit(); }", original)
    page.evaluate('saveOverlay()')
    page.wait_for_function("() => document.getElementById('overlay-save').textContent.includes('Saved')")
    select('webcam')


def check_aspect_scene_change(page):
    """A delayed pointer event must not resize a newly selected scene."""
    grip = page.locator('#ov-stage').locator('.ov-h-e')
    grip.scroll_into_view_if_needed()
    handle = grip.bounding_box()
    x, y = handle['x'] + handle['width']/2, handle['y'] + handle['height']/2
    page.mouse.move(x, y); page.mouse.down()
    page.select_option('#ov-scene', 'intermission')
    page.wait_for_function("() => ovState.page==='intermission'")
    before = page.evaluate('JSON.stringify(ovState.layout)')
    page.mouse.move(x + 50, y + 50); page.mouse.up()
    assert page.evaluate('JSON.stringify(ovState.layout)') == before
    page.select_option('#ov-scene', 'hud')
    page.wait_for_function("() => ovState.page==='hud'")
    page.select_option('#ov-slotpick', 'webcam')


def check_aspect_template(page):
    original = page.evaluate('JSON.stringify(ovState.layout)')
    page.select_option('#ov-slotpick', 'intermission-webcam')
    page.select_option('#ov-aspect-preset', '1:1')
    page.select_option('#ov-template', 'capture-webcam-chat')
    assert page.evaluate("ovState.layout.slots['intermission-webcam'].aspectRatio === undefined")
    field = page.locator('#ov-prop-width')
    field.fill('640'); field.press('Tab')
    assert page.evaluate("ovState.layout.slots['intermission-webcam'].height") == 360
    page.evaluate("raw => { ovState.layout=JSON.parse(raw);ovReloadCanvasFromLayout();ovHistInit(); }", original)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shots', action='store_true')
    parser.add_argument('--browser-executable', help='Use an installed browser with an isolated test profile')
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
                refresh=lambda **kw: (['Intermission Chat'], ''),
                restore=lambda: {'ok': True}))
        server, port = ui._serve(ctx)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=args.browser_executable,
                    args=['--renderer-process-limit=2', '--num-raster-threads=1'])
                page = browser.new_page(viewport={'width': 1600, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(error.stack))
                page.goto(f'http://127.0.0.1:{port}/#profile')
                page.wait_for_function("() => ovState.layout && ovState.slots.length > 0")
                page.evaluate('openBuilderModal()')
                page.wait_for_timeout(150)
                check_aspect_resize(page)
                check_aspect_scene_change(page)
                if args.shots:
                    page.select_option('#ov-slotpick', 'webcam')
                    page.locator('#ov-modal .ovmodal-card').screenshot(
                        path=str(ROOT / 'src/docs/wiki/images/cc-overlay-builder.png'))
                    shutil.copy2(ROOT / 'src/docs/wiki/images/cc-overlay-builder.png',
                                 ROOT / 'src/docs/slides/assets/img/cc-overlay-builder.png')
                page.select_option('#ov-scene', 'intermission')
                page.wait_for_function("() => ovState.page === 'intermission' && ovState.slots.length === 3")
                check_aspect_template(page)
                assert page.locator('#ov-template option').count() == 5
                initial = page.evaluate('JSON.stringify(ovState.layout)')
                assert page.evaluate("() => { try { ovUseTemplate('unsupported'); return true; } catch(e) { return false; } }")
                page.evaluate("() => { overlayPage='hud'; ovUseTemplate('webcam-chat'); overlayPage='intermission'; }")
                assert page.evaluate('JSON.stringify(ovState.layout)') == initial, 'A stale scene must not accept a starter layout'
                page.evaluate("() => { ovState.templates.push({id:'invalid-slot',slots:{'not-a-scene-slot':{visible:true}}}); ovUseTemplate('invalid-slot'); ovState.templates.pop(); ovHistInit(); }")
                assert page.evaluate('JSON.stringify(ovState.layout)') == initial, 'Templates must patch only supported slots'
                page.select_option('#ov-template', 'capture-webcam-chat')
                assert page.evaluate("ovState.layout.slots['intermission-capture'].visible") is True
                assert page.evaluate("ovState.layout.slots.ichat.top") == 400
                assert calls == [], 'Selecting a starter layout must not touch OBS'
                page.click('#overlay-undo')
                assert page.evaluate("ovState.layout.slots['intermission-capture'].visible") is False
                assert page.evaluate("ovState.shadow.getElementById('ichat').getBoundingClientRect().width") > 100
                assert page.evaluate("ovState.shadow.getElementById('ichat-log').textContent.includes('RaceFan')")
                page.evaluate("ovPreviewText('ichat', '')")
                assert page.evaluate("ovState.shadow.getElementById('ichat-log').textContent") == ''
                page.evaluate('ovPreviewReset()')
                if args.shots:
                    page.select_option('#ov-slotpick', 'intermission-webcam')
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
                    assert page.locator('#ov-template option').count() == 3
                    assert not page.locator('#ov-template option[value=webcam-chat]').count()
                    assert not page.evaluate("!!ovState.shadow.getElementById('intermission-webcam')")
                    page.evaluate('saveOverlay()')
                    assert page.locator('#overlay-err').is_hidden()
                    page.select_option('#ov-scene', 'hud')
                    page.wait_for_function("() => ovState.page==='hud' && ovState.slots.length>1")
                    page.evaluate('saveOverlay()')
                    assert page.locator('#overlay-err').is_hidden()
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

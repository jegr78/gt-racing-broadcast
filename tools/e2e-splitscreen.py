#!/usr/bin/env python3
"""Exercise shipped Split builder and labels without real feeds or OBS."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import test_racecast as cli
import test_ui_server as ui
import test_console_gate as gate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shots', action='store_true')
    parser.add_argument('--browser-executable', help='Use an installed browser with an isolated test profile')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    m = cli.m
    with tempfile.TemporaryDirectory(prefix='split-builder-') as td:
        original = cli._mk_active_profile(td)
        profile = Path(td) / 'profiles/demo/profile.env'
        profile.write_text('NAME=Demo Racing\nKIND=endurance\n', encoding='utf-8')
        overlay = profile.parent / 'overlay'
        overlay.mkdir()
        hud_css = '#clock{color:gold}'
        legacy_css = '#split-left{right:880px;top:211px;font-size:32px}'
        (overlay / 'hud.css').write_text(hud_css, encoding='utf-8')
        (overlay / 'splitscreen.css').write_text(legacy_css, encoding='utf-8')
        trace = []
        state = {'current': 'A', 'next_active': True}
        ctx = ui._ctx()
        ctx.update(version='dev build',
            status=lambda: {'relay': {'alive': False}, 'streams': [], 'companion': {'running': False}},
            overlay_asset_serve=m.overlay_asset_serve,
            overlay_font_serve=m.overlay_font_serve,
            update_check=lambda force=False: {'ok': True, 'note': 'development build'},
            profiles=lambda: {'ok': True, 'active': 'demo', 'profiles': [
                {'name': 'demo', 'display': 'Demo Racing', 'kind': 'endurance'}]},
            overlay_slots=m.overlay_slots_data,
            overlay_layout_read=m.overlay_layout_read_data,
            overlay_layout_write=m.overlay_layout_write_data,
            overlay_fonts=m.overlay_fonts_list_data,
            overlay_bg=m.overlay_bg_path,
            overlay_apply=lambda page: m.overlay_apply_data(page,
                set_transform=lambda *a: (trace.append(('transform', a)) or (True, '')),
                set_enabled=lambda *a: (trace.append(('visibility', a)) or (True, '')),
                refresh=lambda **kw: (['Splitscreen Labels', 'Splitscreen HUD Overlay'], '')))
        obs_guard = patch('obs_ws.get_scene_items', return_value=({}, ''))
        obs_guard.start()
        server, port = ui._serve(ctx)
        base_handler = gate.m.make_handler
        def handler(*a, **kw):
            a[0].splitscreen_state = lambda: dict(state)
            kw.update(overlay_dir=str(overlay), hud_path=str(ROOT / 'src/obs/hud.html'),
                      splitscreen_path=str(ROOT / 'src/obs/splitscreen.html'))
            return base_handler(*a, **kw)
        with patch.object(gate.m, 'make_handler', side_effect=handler):
            relay = gate._serve()
        relay_port = relay.server_address[1]
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=args.browser_executable,
                    args=['--renderer-process-limit=2', '--num-raster-threads=1'])
                page = browser.new_page(viewport={'width': 1600, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(error.stack))
                legacy = browser.new_page(viewport={'width': 1920, 'height': 1080})
                legacy.goto(f'http://127.0.0.1:{relay_port}/splitscreen')
                legacy.wait_for_function("() => document.getElementById('split-left').textContent === 'Current'")
                original_label = legacy.locator('#split-left').bounding_box()
                assert abs(original_label['x']+original_label['width']-1040) < 1
                assert original_label['y'] == 211, 'Unedited legacy label CSS must retain viewport coordinates'
                legacy.close()
                page.goto(f'http://127.0.0.1:{port}/#profile')
                page.wait_for_function("() => ovState.layout && ovState.slots.length > 0")
                page.evaluate('openBuilderModal()')
                page.select_option('#ov-scene', 'splitscreen')
                page.wait_for_function("() => ovState.page === 'splitscreen' && ovState.slots.some(s=>s.id==='split-feed-a')")
                assert page.locator('#ov-template option').count() >= 3
                assert not page.evaluate("ovState.slots.some(s=>s.id==='webcam'||s.id.startsWith('tele-'))")
                page.select_option('#ov-template', 'main-secondary')
                assert page.evaluate("ovState.layout.slots['split-feed-a'].width") == 1280
                assert page.evaluate("ovState.layout.slots['split-feed-b'].left") == 1360
                page.click('#overlay-undo')
                page.select_option('#ov-slotpick', 'split-feed-a')
                assert not page.evaluate("'visible' in ovState.fields")
                feed = page.locator('#ov-stage').locator('#split-feed-a')
                box = feed.bounding_box()
                page.mouse.move(box['x']+box['width']/2, box['y']+box['height']/2)
                page.mouse.down()
                page.mouse.move(box['x']+box['width']/2+30, box['y']+box['height']/2+20)
                page.mouse.up()
                assert page.evaluate("ovState.layout.slots['split-feed-a'].left") > 0, 'Feed drag must change its own bounds'
                page.click('#overlay-undo')
                page.select_option('#ov-slotpick', 'split-feed-a')
                assert not page.locator('#ov-aspect-lock').is_checked()
                page.locator('#ov-aspect-lock').check()
                handle = page.locator('#ov-stage').locator('.ov-h-e').bounding_box()
                page.mouse.move(handle['x']+handle['width']/2, handle['y']+handle['height']/2)
                page.mouse.down()
                page.mouse.move(handle['x']+handle['width']/2+25, handle['y']+handle['height']/2)
                page.mouse.up()
                assert page.evaluate("ovState.layout.slots['split-feed-a'].width") > 960, 'Resize handle must change feed bounds'
                resized = page.evaluate("ovState.layout.slots['split-feed-a']")
                assert abs(resized['height'] - resized['width'] * 9 / 16) < 0.0011
                page.click('#overlay-undo')
                for prop, value in [('left', 80), ('top', 100), ('width', 1200), ('height', 675)]:
                    page.evaluate("([p,v]) => { const f=ovState.fields[p]; f.value=v; f.dispatchEvent(new Event('input')); f.dispatchEvent(new Event('change')); }", [prop, value])
                page.select_option('#ov-slotpick', 'split-left')
                for prop, value in [('offsetRight', 24), ('offsetTop', -40)]:
                    page.evaluate("([p,v]) => { const f=ovState.fields[p]; f.value=v; f.dispatchEvent(new Event('input')); f.dispatchEvent(new Event('change')); }", [prop, value])
                # Force an overlap with another editable HUD slot. A different
                # legacy font/position must not accidentally mask the layer test.
                overlap = page.evaluate("""() => {
                  const id='split-left', s=ovState.shadow;
                  const saved=Object.assign({},ovState.layout.slots[id]);
                  const l=s.getElementById(id).getBoundingClientRect(), c=s.getElementById('clock').getBoundingClientRect();
                  ovSetProp(id,'offsetRight',saved.offsetRight+(l.x+l.width/2-c.x-c.width/2)/ovState.scale);
                  ovSetProp(id,'offsetTop',saved.offsetTop+(c.y+c.height/2-l.y-l.height/2)/ovState.scale);
                  const hit=s.elementFromPoint(c.x+c.width/2,c.y+c.height/2)?.id;
                  ovState.layout.slots[id]=saved; ovReloadCanvasFromLayout();
                  return hit;
                }""")
                assert overlap == 'split-left', 'Labels must remain selectable above overlapping HUD content'
                # Real nested pointer events must move the label only. Inverting
                # the x delta preserves its right anchor when the feed resizes.
                before = page.evaluate("JSON.stringify(ovState.layout.slots['split-feed-a'])")
                label = page.locator('#ov-stage').locator('#split-left')
                box = label.bounding_box()
                page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
                hit = page.evaluate("([x,y]) => ovState.shadow.elementFromPoint(x,y)?.id", [box['x']+box['width']/2,box['y']+box['height']/2])
                assert hit == 'split-left', 'HUD must not cover the editable label'
                page.mouse.down()
                page.mouse.move(box['x'] + box['width']/2 + 20, box['y'] + box['height']/2 + 10)
                page.mouse.up()
                assert page.evaluate("JSON.stringify(ovState.layout.slots['split-feed-a'])") == before, 'Label drag must not bubble into feed drag'
                assert page.evaluate("ovState.layout.slots['split-left'].offsetRight") < 24, {'hit':hit,'box':box,'selected':page.evaluate('ovState.sel'),'slot':page.evaluate("ovState.layout.slots['split-left']")}
                assert not page.evaluate("Object.hasOwn(ovState.layout.slots['split-left'], 'left')")
                # Selection outlines for nested labels use canvas coordinates.
                geometry = page.evaluate("() => { const s=ovState.shadow, a=s.getElementById('split-left').getBoundingClientRect(), b=ovState.selBox.getBoundingClientRect(); return [a.x-b.x,a.y-b.y]; }")
                assert all(abs(delta) < 1.1 for delta in geometry), geometry
                assert trace == [], 'Editing must not touch OBS'
                page.evaluate('saveOverlay()')
                page.wait_for_function("() => document.getElementById('overlay-save').textContent.includes('Saved')")
                saved = json.loads((overlay / 'layout-splitscreen.json').read_text(encoding='utf-8'))
                assert saved['slots']['split-feed-a']['width'] == 1200
                assert (overlay / 'hud.css').read_text(encoding='utf-8') == hud_css
                page.evaluate('applyOverlay()')
                page.wait_for_timeout(200)
                assert trace and all(kind == 'transform' and args[0] == 'Splitscreen' for kind, args in trace), trace
                if args.shots:
                    # Document the clean starter layout; this changes the draft
                    # only, preserving the saved legacy fixture below.
                    draft = page.evaluate('JSON.stringify(ovState.layout)')
                    page.evaluate("() => { ovState.layout.customCss=''; document.getElementById('overlayCss').value=''; }")
                    page.select_option('#ov-template', 'main-secondary')
                    page.select_option('#ov-slotpick', 'split-feed-a')
                    page.wait_for_function("() => !document.getElementById('overlay-save').disabled")
                    page.locator('#ov-modal .ovmodal-card').screenshot(path=str(ROOT / 'src/docs/wiki/images/cc-splitscreen-builder.png'))
                    page.evaluate("draft => { ovState.layout=JSON.parse(draft); document.getElementById('overlayCss').value=ovState.layout.customCss; ovReloadCanvasFromLayout(); }", draft)
                live = browser.new_page(viewport={'width': 1920, 'height': 1080})
                live.on('pageerror', lambda error: errors.append(error.stack))
                live.goto(f'http://127.0.0.1:{relay_port}/splitscreen')
                live.wait_for_function("() => document.getElementById('split-left').textContent === 'Current'")
                def live_geometry():
                    return live.evaluate("() => ['split-feed-a','split-feed-b','split-left','split-right'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return [r.x,r.y,r.width,r.height]})")
                first = live_geometry()
                assert first[0] == [80, 100, 1200, 675], first
                label = saved['slots']['split-left']
                assert abs(first[2][0]+first[2][2]-(1360-label['offsetRight'])) < 1
                assert abs(first[2][1]-(41+label['offsetTop'])) < 1, 'Legacy anchor plus new offset must follow moved feed'
                state.update(current='B')
                live.wait_for_function("() => document.getElementById('split-right').textContent === 'Current'")
                assert live_geometry() == first, 'CURRENT/NEXT must never swap feed or label geometry'
                state.update(next_active=False)
                live.wait_for_function("() => document.getElementById('split-left').hidden")
                state.update(current='A', next_active=True)
                live.wait_for_function("() => !document.getElementById('split-left').hidden")
                # Builder preference remains hidden even when polling unhides the
                # CURRENT element. Saved CSS, rather than a JS test stub, owns it.
                saved['slots']['split-left']['visible'] = False
                assert m.overlay_layout_write_data('splitscreen', saved)['ok']
                live.reload()
                live.wait_for_function("() => document.getElementById('split-left').textContent === 'Current'")
                assert live.locator('#split-left').evaluate("el=>getComputedStyle(el).display") == 'none'
                page.reload()
                page.wait_for_function("() => ovState.page === 'hud' && ovState.slots.length > 0")
                page.evaluate('openBuilderModal()')
                page.select_option('#ov-scene', 'splitscreen')
                page.wait_for_function("() => ovState.page === 'splitscreen' && ovState.layout.slots['split-feed-a'].width === 1200")
                profile.write_text('NAME=Demo Racing\nKIND=solo\nTEMPLATE=pov\n', encoding='utf-8')
                changed = page.evaluate("() => { applyKindGating({ok:true,active:'demo',profiles:[{name:'demo',kind:'solo',template:'pov'}]}); return overlayPage; }")
                assert changed == 'hud', 'Changing to Solo must leave unsupported Split editor'
                page.wait_for_function("() => ovState.page === 'hud'")
                assert not page.locator('#ov-scene option[value=splitscreen]').is_visible()
                assert not errors, errors
                browser.close()
            print(json.dumps({'ok': True, 'obsMutations': trace, 'browserErrors': errors}))
        finally:
            obs_guard.stop()
            server.shutdown()
            server.server_close()
            relay.shutdown()
            relay.server_close()
            m._env_base, m._runtime_base_dir = original


if __name__ == '__main__':
    main()

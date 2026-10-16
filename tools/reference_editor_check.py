#!/usr/bin/env python3
"""Exercise the shared track editor in an empty, isolated analysis profile."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import e2e
from test_reference_track_editor import tracks


async def verify(base, home, shots):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1050})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(base + '/telemetry?profile=solo-pov#settings')
        await page.wait_for_function('() => tmState.loaded && currentView === "settings"')
        assert not await page.evaluate('() => tmState.lapB'), 'editor fixture must have no analysed lap'
        await page.locator('#tm-reference-layouts summary').click()
        await page.wait_for_function('() => !document.getElementById("tm-reference-edit").disabled')
        assert await page.locator('#tm-reference-layout option').count() == 2
        for reverse in (True, False):
            value = await page.evaluate('(reverse) => String(tmReferenceLayouts.rows.findIndex(r => r.reverse === reverse))', reverse)
            await page.locator('#tm-reference-layout').select_option(value)
            await page.locator('#tm-reference-edit').click()
            await page.wait_for_function('() => !!tmEditor.draft')
            assert await page.evaluate('() => tmEditor.reply.reverse') == reverse
            assert not await page.locator('#tm-editor-reviewed').is_checked()
            assert 'reference' in (await page.get_by_label('Provenance notes').input_value()).lower()
            assert await page.locator('#tm-editor-map path').count()
            if reverse:
                await page.locator('#tm-editor-close').click()
        await page.get_by_label('Numbering scheme', exact=True).fill('Reference review · coaching sectors')
        await page.locator('#tm-editor-reviewed').check()
        await page.locator('#tm-editor-apply').click()
        await page.wait_for_function('() => tmEditor.reply?.revision === 1 && !tmEditor.busy')
        assert 'Applied shared definition' in await page.locator('#tm-editor-status').inner_text()
        if shots:
            await page.screenshot(path=str(ROOT / 'src/docs/wiki/images/telemetry-reference-track-editor.png'))
        await page.locator('#tm-editor-close').click()
        await page.locator('#tm-reference-edit').click()
        await page.wait_for_function('() => tmEditor.reply?.revision === 1 && !!tmEditor.draft')
        assert await page.get_by_label('Numbering scheme', exact=True).input_value() == 'Reference review · coaching sectors'
        await page.locator('#tm-editor-close').click()
        for width in (1440, 1024, 390):
            await page.set_viewport_size({'width': width, 'height': 1050})
            assert await page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'), width
        assert not errors, errors
        assert not list((home / 'runtime').glob('*/telemetry-recordings/*.gt7rec'))
        await browser.close()
    print('PASS reference editor: no recording, both directions, unreviewed proposals, apply and reopen')
    return None


async def main(shots=False):
    with tempfile.TemporaryDirectory(prefix='racecast-reference-editor-') as temp:
        _launcher, home = e2e._pov_home(temp, None)
        home = Path(home)
        # Synthetic downloaded geometry replaces only this throwaway app's bundled data.
        tracks(str(home / 'src/assets/gt7'))
        port = e2e.E.free_port()
        env = dict(os.environ, RACECAST_UI_PORT=str(port), PYTHONUNBUFFERED='1')
        env.pop('RACECAST_PROFILE', None)
        source = str(home / 'src')
        code = "import sys;sys.path.insert(0,sys.argv[1]);import racecast as rc;rc._relay_record_status=lambda:None;sys.argv=['racecast',*sys.argv[2:]];rc.main()"
        log = home / 'reference-editor.log'
        proc = e2e._spawn([sys.executable, '-c', code, source, 'telemetry', 'ui', '--no-browser'], env, str(log), cwd=str(home))
        try:
            base = 'http://127.0.0.1:' + str(port)
            e2e._wait_ready(base + '/api/ping', 30, proc, str(log))
            await verify(base, home, shots)
        except Exception:
            print(log.read_text(encoding='utf-8', errors='replace'))
            raise
        finally:
            e2e._kill(proc)
    return None


if __name__ == '__main__':
    asyncio.run(main('--shots' in sys.argv))

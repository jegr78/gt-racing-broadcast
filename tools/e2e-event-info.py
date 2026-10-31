#!/usr/bin/env python3
"""Exercise the signed Event Notes/Documents dialog on every crew desk."""
import argparse
import asyncio
import base64
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT/'tools')]
import e2e_safety  # noqa: E402
from engineer_ui_fixture import Fixture, gate  # noqa: E402
import _obs_guard  # noqa: E402

DOCUMENTS = [
    {'title': 'Season 21 Rulebook', 'url': 'https://docs.google.com/document/d/EXAMPLE/edit?usp=sharing#heading=h.rules'},
    {'title': 'Round 4 Event Document', 'url': 'https://docs.google.com/document/d/e/EXAMPLE/pub'},
    {'title': 'Circuit briefing (PDF)', 'url': 'https://example.test/briefing.pdf#page=3'},
]
NOTES = [{'heading': 'Broadcast briefing', 'note': 'Mention our league partners before the start.', 'priority': 'important'}]


class SyntheticObs(_obs_guard.NoObs):
    """Read-only OBS stand-in; the real connection tripwire remains installed."""
    def probe(self, **kwargs):
        return True, ''

    def get_health_stats(self, **kwargs):
        # No stream transition or external notification in the fixture.
        return True, {}, ''

    def get_program_screenshot(self, **kwargs):
        return (ROOT/'src/assets/preview-bg.jpg').read_bytes(), ''

    def get_scene_items(self, scene, **kwargs):
        return {name: key == 'chat' for key, name in gate.m.intermission_live.SOURCES.items()}, ''

    def read_obs_state(self, sources, inputs, **kwargs):
        return dict(scene='Stint',
            sources=[dict(scene=scene,source=source,enabled=source in ('Feed A','HUD Browser')) for scene,source in sources],
            audio=[dict(input=name,muted=False,volumeDb=-6) for name in inputs],
            stream=dict(active=True,reconnecting=False,timecode='00:18:31')), ''


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shots', action='store_true')
    parser.add_argument('--browser-executable', help='Use an installed browser with an isolated test profile.')
    args = parser.parse_args()
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix='racecast-event-info-') as root:
        fixture = Fixture(root)
        original_obs = gate.m._obs_ws
        gate.m._obs_ws = SyntheticObs()
        fixture.access().update(enabled=True, expected=0)
        notes = gate.m.EventNotesSource(None); notes.rows = list(NOTES)
        documents = gate.m.EventDocumentsSource(None); documents.rows = list(DOCUMENTS)
        server = fixture.console(with_live=True, event_notes_source=notes, event_documents_source=documents)
        fixture.relay.source.rows = [('https://youtu.be/EXAMPLE', 'Producer', '1', 2),
                                     ('https://youtu.be/NEXT', 'Next commentator', '2', 3)]
        fixture.relay.source.items = [row[0] for row in fixture.relay.source.rows]
        fixture.relay.feeds['A'].paused = False
        fixture.relay.feeds['A'].phase = 'serving'
        fixture.relay.feeds['A'].served_ok = True
        cookies = Path(root)/'synthetic-cookies.txt'; cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        fixture.relay.cookies = str(cookies)
        fixture.feed_live()
        token = gate.m.console_auth.mint_token(gate.SECRET, 'producer')
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=args.browser_executable,
                    args=['--renderer-process-limit=2', '--num-raster-threads=1'])
                context = await browser.new_context(viewport={'width':1440, 'height':1000})
                external = []
                font_css = ''.join('@font-face{font-family:"'+family+'";font-weight:100 900;src:url(data:font/woff2;base64,'+
                    base64.b64encode((ROOT/'src/docs/slides/assets/fonts'/file).read_bytes()).decode()+')}'
                    for family, file in [('Saira Condensed','SairaCondensed.woff2'),('IBM Plex Mono','IBMPlexMono.woff2')])
                async def public_document(route):
                    if route.request.url.startswith('https://fonts.googleapis.com/'):
                        await route.fulfill(content_type='text/css', body=font_css)
                    elif route.request.url.startswith(base):
                        await route.continue_()
                    else:
                        external.append(dict(url=route.request.url, headers=route.request.headers))
                        await route.fulfill(content_type='text/html', body='<title>Public document fixture</title>Public document')
                await context.route('**/*', public_document)
                unauthenticated = await context.request.get(base+'/console/event-notes/data')
                assert unauthenticated.status == 401
                cases = [('/panel', None), ('/console/panel', 'director-panel'),
                         ('/console/cockpit', 'console-cockpit'), ('/console/race-control', 'console-race-control'),
                         ('/console/race-engineer', None)]
                for path, shot in cases:
                    notes.rows = list(NOTES); documents.rows = list(DOCUMENTS)
                    page = await context.new_page(); errors = []
                    page.on('pageerror', lambda error, errors=errors: errors.append(str(error)))
                    await page.clock.install()
                    notes_status = []
                    page.on('response', lambda r, seen=notes_status: seen.append(r.status)
                            if '/event-notes/data' in r.url else None)
                    await page.goto(base+path+'?t='+token)
                    try:
                        await page.locator('#notesBtn').wait_for(state='visible')
                    except Exception as exc:  # noqa: BLE001  name what the page saw (#1031)
                        raise AssertionError(f'#notesBtn never shown on {path}: notes responses '
                                             f'{notes_status}, page errors {errors}') from exc
                    if args.shots and shot:
                        # The production markup and synthetic HTTP callbacks own the page.
                        await page.wait_for_timeout(2500)
                        target = ROOT/'src/docs/wiki/images'/f'{shot}.png'
                        await page.screenshot(path=str(target), full_page=True)
                        slide = ROOT/'src/docs/slides/assets/img'/target.name
                        if slide.exists(): shutil.copyfile(target, slide)
                    await page.locator('#notesBtn').click()
                    assert await page.locator('#eventNotesTab').get_attribute('aria-selected') == 'true'
                    assert 'league partners' in await page.locator('#notesBody').inner_text()
                    await page.locator('#eventNotesTab').press('End')
                    assert await page.locator('#eventDocumentsTab').get_attribute('aria-selected') == 'true'
                    assert await page.locator('#documentsBody a').count() == 3
                    assert not external, ('A dialog must not fetch or preview any document', path, external)
                    if args.shots and path == '/panel':
                        await page.locator('#notesModal').screenshot(path=str(ROOT/'src/docs/wiki/images/event-info-documents.png'))
                    for i, doc in enumerate(DOCUMENTS):
                        link = page.locator('#documentsBody a').nth(i)
                        assert await link.get_attribute('href') == doc['url']
                        assert await link.get_attribute('target') == '_blank'
                        assert set((await link.get_attribute('rel')).split()) == {'noopener', 'noreferrer'}
                        async with page.expect_popup() as opened:
                            await link.click()
                        popup = await opened.value
                        await popup.wait_for_load_state()
                        assert popup.url == doc['url']
                        assert await popup.evaluate('window.opener === null')
                        await popup.close()
                    assert len(external) == 3 and all('referer' not in r['headers'] for r in external)
                    external.clear()
                    await page.locator('#eventDocumentsTab').press('Home')
                    assert await page.locator('#eventNotesTab').get_attribute('aria-selected') == 'true'
                    await page.locator('#eventNotesTab').press('ArrowRight')
                    # Both tabs and every link fit the phone viewport without a second toolbar control.
                    await page.set_viewport_size({'width':390, 'height':844})
                    box = await page.locator('#notesModal').bounding_box()
                    assert box['x'] >= 0 and box['x']+box['width'] <= 391 and box['height'] <= 844, box
                    await page.locator('#eventInfoClose').click()
                    # A temporary API failure leaves the last good list usable.
                    async def unavailable(route):await route.fulfill(status=503, json={'error':'temporary'})
                    await page.route('**/event-notes/data', unavailable)
                    await page.clock.fast_forward(31000)
                    await page.wait_for_timeout(100)
                    assert await page.locator('#documentsBody a').count() == 3
                    await page.unroute('**/event-notes/data', unavailable)
                    # Document-only Sheets keep the same button and open Documents directly.
                    notes.rows = []; documents.rows = DOCUMENTS[:1]
                    await page.clock.fast_forward(31000)
                    await page.wait_for_function('document.querySelectorAll("#documentsBody a").length === 1')
                    await page.locator('#notesBtn').click()
                    assert await page.locator('#eventDocumentsTab').get_attribute('aria-selected') == 'true'
                    await page.keyboard.press('Escape')
                    # Labels remain plain text and executable URL schemes are excluded twice.
                    documents.rows = [{'title':'<img src=x onerror="window.documentInjected=true">', 'url':DOCUMENTS[0]['url']},
                                      {'title':'Unsafe', 'url':'javascript:window.documentInjected=true'}]
                    await page.clock.fast_forward(31000)
                    await page.wait_for_function('document.querySelector("#documentsBody a")?.textContent.startsWith("<img")')
                    assert await page.locator('#documentsBody a').count() == 1
                    assert not await page.locator('#documentsBody img').count()
                    assert not await page.evaluate('!!window.documentInjected')
                    documents.rows = []
                    await page.clock.fast_forward(31000)
                    await page.locator('#notesBtn').wait_for(state='hidden')
                    # Legacy notes-only Sheets still work, including the empty Documents tab.
                    notes.rows = list(NOTES)
                    await page.clock.fast_forward(31000)
                    await page.locator('#notesBtn').wait_for(state='visible')
                    await page.locator('#notesBtn').click()
                    assert await page.locator('#eventNotesTab').get_attribute('aria-selected') == 'true'
                    await page.locator('#eventDocumentsTab').click()
                    assert 'No documents.' in await page.locator('#documentsBody').inner_text()
                    # A revoked console session clears the dialog instead of retaining stale state.
                    async def forbidden(route):await route.fulfill(status=403, json={'error':'forbidden'})
                    await page.route('**/event-notes/data', forbidden)
                    await page.clock.fast_forward(31000)
                    await page.locator('#notesBtn').wait_for(state='hidden')
                    assert not await page.locator('#notesModal').evaluate('(el) => el.open')
                    assert not errors, (path, errors)
                    await page.close()
                # Restoring a suspended tab must not create duplicate poll loops
                # or publish a response from before its suspension.
                held, release = asyncio.Event(), asyncio.Event()
                calls = []
                async def delayed(route):
                    calls.append(route.request.url)
                    first = len(calls) == 1
                    if first:
                        held.set(); await release.wait()
                    data = {'notes': [], 'documents': [{'title': 'Old document' if first else 'Current document', 'url': DOCUMENTS[0]['url']}]}
                    try: await route.fulfill(json=data)
                    except Exception: pass  # Suspending the page already aborted the first request.
                page = await context.new_page()
                await page.clock.install()
                await page.route('**/event-notes/data', delayed)
                try:
                    await page.goto(base+'/console/panel?t='+token)
                    await asyncio.wait_for(held.wait(), 5)
                    await page.evaluate('dispatchEvent(new PageTransitionEvent("pagehide", {persisted:true}))')
                    await page.clock.fast_forward(61000)
                    assert len(calls) == 1
                    await page.evaluate('dispatchEvent(new PageTransitionEvent("pageshow", {persisted:true}))')
                    await page.wait_for_function('document.querySelector("#documentsBody a")?.textContent === "Current document"')
                    release.set(); await page.wait_for_timeout(50)
                    assert await page.locator('#documentsBody a').inner_text() == 'Current document'
                    await page.clock.fast_forward(31000)
                    await page.wait_for_timeout(100)
                    assert len(calls) == 3, 'Suspension/restore created duplicate pollers'
                finally:
                    release.set(); await page.close()
                await browser.close()
            assert not _obs_guard.CALLS, 'A fixture attempted real OBS access'
        finally:
            server.shutdown(); server.server_close(); fixture.live.close()
            gate.m._obs_ws = original_obs
    print(json.dumps(dict(ok=True, desks=4, entry_points=5, external_document_fetches='user clicks only', real_obs_attempts=0)))
    return 0


if __name__ == '__main__':
    e2e_safety.MAX_SECONDS = 240
    result = e2e_safety.enter(__file__, sys.argv[1:])
    if result is not None:raise SystemExit(result)
    raise SystemExit(asyncio.run(main()))

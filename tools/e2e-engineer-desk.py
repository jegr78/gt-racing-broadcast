#!/usr/bin/env python3
"""Real signed Engineer desk, shared synthetic preparation and independent browsers."""
import asyncio
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'tools'))
import e2e_visual
from engineer_ui_fixture import Fixture, gate


async def main():
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix='racecast-desk-browser-') as root:
        fixture=Fixture(root)
        fixture.access().update(enabled=True,expected=0)
        server=fixture.console(with_live=True); fixture.feed_live()
        try:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                page=await browser.new_page(viewport={'width':1440,'height':1050})
                token=gate.m.console_auth.mint_token(gate.SECRET,'engineer')
                await page.goto(f'http://127.0.0.1:{server.server_port}/console/race-engineer?t={token}')
                await page.wait_for_function('() => document.getElementById("speed")?.textContent.includes("180")',timeout=5000)
                assert await page.locator('#rpm').inner_text() == '7000'
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.wait_for_function('() => EngineerDesk.state.pair?.state === "ready"')
                assert await page.locator('#chart path').count() >= 2
                assert await page.locator('#sectors tbody tr').first.locator('td').nth(2).inner_text() == 'Unavailable'
                assert await page.locator('#zoom-to').input_value() == '1000'
                old=fixture.live.snapshot()['laps'][1]['id']
                await page.locator('#lap-b').select_option(old)
                assert not await page.locator('#follow').is_checked()
                await page.wait_for_function('(id) => EngineerDesk.state.detail?.id === id',arg=old)
                await page.locator('#zoom-from').fill('100');await page.locator('#zoom-from').press('Tab')
                await page.locator('#zoom-to').fill('700');await page.locator('#zoom-to').press('Tab')
                assert await page.evaluate('() => EngineerDesk.state.zoom') == [100,700]
                context=gate.m.gt7_context.Store.prepared(str(fixture.root/fixture.profile/'telemetry-recordings'))
                doc=context.read();doc['data']['notes']=[{'id':'ui-note','scope':'lap','session':1,'lap':2,'text':'Producer note revision'}]
                context.save(doc['data'],doc['revision'])
                await page.wait_for_function('() => document.getElementById("notes").textContent.includes("Producer note revision")')
                assert await page.evaluate('() => EngineerDesk.state.zoom') == [100,700]
                other=await browser.new_page(viewport={'width':1024,'height':900})
                await other.goto(f'http://127.0.0.1:{server.server_port}/console/race-engineer?t={token}')
                await other.wait_for_function('() => EngineerDesk.state.detail?.state === "provisional"')
                assert await other.locator('#follow').is_checked()
                fixture.feed_live()
                await page.wait_for_function('() => EngineerDesk.state.newLaps > 0')
                assert await page.evaluate('() => EngineerDesk.state.b') == old
                assert await page.evaluate('() => EngineerDesk.state.zoom') == [100,700]
                fixture.live_clock=16.0
                await page.wait_for_function('() => document.getElementById("status").textContent === "Telemetry stale"')
                assert await page.locator('#protected').evaluate('(el) => el.classList.contains("dim")')
                packet=fixture.live._replay.eng._last
                fixture.live.put(packet._replace(paused=True),fixture.live._receipt[1]+.1);fixture.live.flush()
                await page.wait_for_function('() => document.getElementById("status").textContent === "Game paused"')
                fixture.live.put(packet._replace(rpm=float('nan'),on_track=False),fixture.live._receipt[1]+.1);fixture.live.flush()
                await page.wait_for_function('() => document.getElementById("status").textContent === "Game menu"')
                assert await page.locator('#rpm').inner_text() == 'Unavailable'
                source=gate.m.gt7_context.source_identity(fixture.live_path)
                fixture.access().update(grant=source,label='<img src=x onerror="window.injected=true">',expected=1)
                await page.wait_for_function('(id) => [...document.getElementById("reference-source").options].some(o=>o.value===id)',arg=source)
                await page.locator('#reference-source').select_option(source)
                await page.wait_for_function('() => EngineerDesk.state.refLaps.length > 0')
                await page.wait_for_function('() => EngineerDesk.state.pair?.state === "ready"')
                assert not await page.evaluate('() => !!window.injected')
                ref_captured=asyncio.Event();ref_release=asyncio.Event()
                async def held_reference(route):
                    response=await route.fetch();data=await response.json();ref_captured.set();await ref_release.wait()
                    await route.fulfill(json=data)
                await page.route('**/race-engineer/reference?*',held_reference)
                await asyncio.wait_for(ref_captured.wait(),timeout=5)
                before=await page.evaluate('() => EngineerDesk.state.received')
                await asyncio.sleep(.6)
                assert await page.evaluate('() => EngineerDesk.state.received') > before, 'detail request blocked current-value refresh'
                fixture.access().update(revoke=source,expected=2)
                await page.wait_for_function('() => EngineerDesk.state.refSource === "" && document.getElementById("notice").textContent.includes("revoked")')
                ref_release.set();await page.unroute_all(behavior='wait')
                await page.wait_for_function('() => !EngineerDesk.state.analysisBusy')
                assert await page.evaluate('() => EngineerDesk.state.refLaps.length') == 0, 'late revoked reference restored history'
                await page.locator('#chatin').fill('Synthetic crew feedback <script>safe</script>')
                await page.locator('#chatsend').click()
                await page.wait_for_function('() => document.getElementById("chatlog").textContent.includes("Synthetic crew feedback")')
                # Disconnect and restore actual HTTP reads; retain selected identity and zoom.
                selected=await page.evaluate('() => EngineerDesk.state.b')
                await page.locator('#zoom-from').fill('100');await page.locator('#zoom-from').press('Tab')
                await page.locator('#zoom-to').fill('700');await page.locator('#zoom-to').press('Tab')
                async def disconnected(route): await route.abort()
                await page.route('**/race-engineer/snapshot',disconnected)
                await page.wait_for_function('() => !EngineerDesk.state.connected')
                assert await page.evaluate('() => EngineerDesk.state.b') == selected
                await page.unroute('**/race-engineer/snapshot',disconnected)
                await page.wait_for_function('() => EngineerDesk.state.connected')
                assert await page.evaluate('() => EngineerDesk.state.zoom') == [100,700]
                fixture.live.put(packet,fixture.live._receipt[1]+.1);fixture.live.flush()
                fixture.access().update(grant=source,label='Practice reference',expected=3)
                await page.wait_for_function('() => document.getElementById("status").textContent === "Telemetry live"')
                await page.wait_for_function('() => document.getElementById("references").textContent.includes("Practice reference")')
                for width,height,name in [(1440,1050,'desktop'),(1024,1100,'tablet'),(390,900,'phone')]:
                    await page.set_viewport_size({'width':width,'height':height})
                    assert await page.evaluate('() => document.documentElement.scrollWidth <= innerWidth'),name
                    await page.evaluate('() => window.scrollTo(0,0)')
                    facts=await page.evaluate((ROOT/'tools/visual-probe.js').read_text())
                    findings=e2e_visual.evaluate(facts,errors)
                    assert not findings,(name,findings)
                    if '--shots' in sys.argv:
                        await page.screenshot(path=str(ROOT/f'src/docs/wiki/images/console-race-engineer-{name}.png'),full_page=True)
                # The same handler now reads a new real relay generation.
                import race_engineer_live as live
                import race_engineer_api as api
                from test_race_engineer_live import FakeTracks,FakeCars
                previous=fixture.live
                fixture.live=live.Store(lambda:fixture.access().read()['enabled'],tracks=FakeTracks(),cars=FakeCars(),
                    runtime_base=str(fixture.root),queue_max=4096,clock=lambda:fixture.live_clock)
                fixture.live.api=api.Service(fixture.live)
                fixture.telemetry.engineer=fixture.live;fixture.feed_live();previous.close()
                await page.wait_for_function('(generation) => EngineerDesk.state.generation === generation',arg=fixture.live.generation)
                assert await page.evaluate('() => EngineerDesk.state.zoom') is None
                assert 'new live generation' in await page.locator('#notice').inner_text()
                chosen=fixture.live.snapshot()['laps'][1]['id']
                await page.locator('#lap-b').select_option(chosen)
                await page.wait_for_function('(id) => EngineerDesk.state.detail?.id === id',arg=chosen)
                # Hold an already prepared protected response while access is revoked.
                captured=asyncio.Event();release=asyncio.Event()
                async def delayed(route):
                    response=await route.fetch();data=await response.json();captured.set();await release.wait()
                    try: await route.fulfill(json=data)
                    except Exception: pass  # Authorization loss already aborted the browser request.
                await page.route('**/race-engineer/lap?*',delayed)
                await captured.wait()
                fixture.crew.inject_row(1,race_engineer=False)
                await page.wait_for_function('() => EngineerDesk.state.stopped')
                assert await page.locator('#protected').evaluate('(el) => el.hidden'), 'revocation left protected view visible'
                release.set();await page.unroute_all(behavior='wait')
                count=[];page.on('request',lambda r:count.append(r.url))
                await asyncio.sleep(1.2)
                assert not count, 'role removal did not stop protected polling'
                assert not await page.locator('#chart path').count() and await page.locator('#speed').inner_text() == 'Unavailable'
                fixture.crew.inject_row(1,race_engineer=True)
                await page.goto(f'http://127.0.0.1:{server.server_port}/console/race-engineer?t={token}')
                await page.wait_for_selector('#protected:not([hidden])')
                fixture.access().update(enabled=False,expected=4)
                await page.wait_for_function('() => EngineerDesk.state.stopped')
                assert await page.locator('#protected').evaluate('(el) => el.hidden'), 'revocation left protected view visible'
                assert 'disabled' in await page.locator('#status').inner_text()
                fixture.access().update(enabled=True,expected=5);fixture.feed_live()
                await page.goto(f'http://127.0.0.1:{server.server_port}/console/race-engineer?t={token}')
                await page.wait_for_function('() => EngineerDesk.state.pair?.state === "ready"')
                source_captured=asyncio.Event();source_release=asyncio.Event()
                async def held_source(route):
                    response=await route.fetch();data=await response.json();source_captured.set();await source_release.wait()
                    try: await route.fulfill(json=data)
                    except Exception: pass  # Access loss already aborted this response.
                await page.route('**/race-engineer/lap?*',held_source)
                await asyncio.wait_for(source_captured.wait(),timeout=5)
                fixture.console_access.available=False
                await page.wait_for_function('() => document.getElementById("status").textContent.includes("unavailable")')
                assert await page.evaluate('() => EngineerDesk.state.stopped && document.getElementById("protected").hidden'), 'source authorization loss retained protected data'
                source_release.set();await page.unroute_all(behavior='wait')
                assert await page.evaluate('() => EngineerDesk.state.snapshot === null && !EngineerDesk.state.detail')
                assert not await page.locator('#chart path').count()
                assert not errors,errors
                await browser.close()
        finally:
            server.shutdown();server.server_close();fixture.live.close()
    print('PASS Engineer desk live, selection, context, reconnect, revocation and three responsive views')
    return None


if __name__=='__main__':asyncio.run(main())

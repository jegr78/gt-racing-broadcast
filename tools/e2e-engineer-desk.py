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

PROGRAM_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900">'
               '<defs><linearGradient id="s" x2="0" y2="1"><stop offset="0" stop-color="#3d5a80"/>'
               '<stop offset="1" stop-color="#98c1d9"/></linearGradient></defs>'
               '<rect width="1600" height="560" fill="url(#s)"/><rect y="560" width="1600" height="340" fill="#3a3f47"/>'
               '<path d="M0 900 L700 560 H900 L1600 900Z" fill="#23272e"/>'
               '<rect x="660" y="640" width="280" height="110" rx="18" fill="#c0392b"/>'
               '<rect x="40" y="40" width="360" height="70" rx="8" fill="#0b1018" opacity=".8"/>'
               '<text x="64" y="88" font-family="sans-serif" font-size="38" fill="#eef4fc">P15 · LAP 3</text></svg>')


async def main():
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix='racecast-desk-browser-') as root:
        fixture=Fixture(root)
        fixture.access().update(enabled=True,expected=0)
        server=fixture.console(with_live=True)
        if '--shots' in sys.argv:
            from test_gt7_track_definitions import definition
            fixture.live._definitions.save(definition(), 0)
        fixture.feed_live()
        import gt7_recording, gt7_telemetry
        wall, _, raw = list(gt7_recording.Recording(fixture.live_path).packets())[-1]
        fixture.live.put(gt7_telemetry.parse_packet(raw)._replace(race_position=15, car_count=15), wall + .02)
        assert fixture.live.flush(10)
        try:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                page=await browser.new_page(viewport={'width':1440,'height':1050})
                token=gate.m.console_auth.mint_token(gate.SECRET,'engineer')
                await page.goto(f'http://127.0.0.1:{server.server_port}/console/race-engineer?t={token}')
                await page.wait_for_function('() => document.getElementById("speed")?.textContent.includes("180")',timeout=5000)
                assert await page.locator('#rpm').inner_text() == '7000'
                assert await page.locator('#race-position').inner_text() == 'P15/15', 'measured race position missing'
                assert 'finish P3/15' in await page.evaluate('() => lapLabel({lap:2,state:"confirmed",time_s:20,line_position:3,line_car_count:15,comparison_eligible:true})'), 'lap option lost line position'
                await page.wait_for_function('() => EngineerDesk.state.historyRevision !== null')
                assert 'Mitsubishi Lancer Evolution IX' in await page.locator('#session option:checked').inner_text(), 'session car name missing'
                fallback = await page.evaluate('() => {const saved=EngineerDesk.state.sessions;EngineerDesk.state.sessions=saved.map(r=>({...r,car:null}));selections();const label=document.querySelector("#session option:checked").textContent;EngineerDesk.state.sessions=saved;selections();return label;}')
                assert 'car 3424' in fallback, 'unknown car name did not fall back to id'
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.wait_for_function('() => EngineerDesk.state.pair?.state === "ready"')
                await page.locator('.mini-details summary').click()
                assert await page.locator('#chart path').count() >= 2
                assert await page.locator('#sectors tbody tr').first.locator('td').nth(2).inner_text() == 'Not yet driven'
                assert await page.locator('#zoom-to').input_value() == '1000'
                # A pending comparison must still use a completed lap as the outline.
                async def pending_pair(route):
                    await route.fulfill(json=dict(version=1, generation=fixture.live.generation, state='pending'))
                await page.route('**/race-engineer/compare?*', pending_pair)
                await page.wait_for_function('() => document.getElementById("selection-summary").textContent === "Preparing selected comparison"')
                assert await page.locator('#zoom-to').input_value() == '1000', 'pending comparison lost full-lap distance'
                assert await page.locator('#map path').first.get_attribute('data-outline') == 'complete', 'pending comparison lost stable outline'
                outline_entered, outline_release = asyncio.Event(), asyncio.Event()
                selected_outline = await page.evaluate('() => EngineerDesk.state.a')
                async def held_outline(route):
                    from urllib.parse import parse_qs, urlsplit
                    if parse_qs(urlsplit(route.request.url).query).get('id', [''])[0] == selected_outline:
                        response = await route.fetch(); data = await response.json()
                        outline_entered.set(); await outline_release.wait()
                        await route.fulfill(json=data)
                    else:
                        await route.continue_()
                stable_outline = await page.locator('#map path').first.get_attribute('d')
                await page.route('**/race-engineer/lap?*', held_outline)
                await asyncio.wait_for(outline_entered.wait(), timeout=5)
                received = await page.evaluate('() => EngineerDesk.state.received')
                await page.wait_for_function('(r) => EngineerDesk.state.received > r', arg=received)
                assert await page.locator('#map path').first.get_attribute('d') == stable_outline, 'snapshot marker refresh discarded a pending complete outline'
                outline_release.set(); await page.unroute('**/race-engineer/lap?*', held_outline)
                assert await page.locator('#chart text').all_inner_texts() and '1000 m' in await page.locator('#chart').text_content()
                assert '1000 m' in await page.locator('#delta-chart').text_content(), 'empty delta lost full-lap range'
                # No eligible A: use the latest confirmed session lap independently.
                async def no_eligible_reference(route):
                    response = await route.fetch()
                    data = await response.json()
                    for row in data.get('laps', []):
                        row['comparison_eligible'] = False
                    await route.fulfill(json=data)
                await page.route('**/race-engineer/history*', no_eligible_reference)
                await page.evaluate('() => {EngineerDesk.state.historyRevision = null;}')
                await page.wait_for_function('() => EngineerDesk.state.laps.length && EngineerDesk.state.laps.every(r => !r.comparison_eligible)')
                await page.locator('#lap-a').select_option('')
                latest_confirmed = [r for r in fixture.live.snapshot()['laps'] if r['state'] == 'confirmed'][-1]['id']
                await page.wait_for_function('(id) => !EngineerDesk.state.a && EngineerDesk.state.outline?.id === id', arg=latest_confirmed)
                assert await page.locator('#zoom-to').input_value() == '1000'
                assert await page.locator('#chart path').nth(1).get_attribute('d') == '', 'fallback outline masqueraded as selected A'
                await page.unroute('**/race-engineer/history*', no_eligible_reference)
                await page.evaluate('() => {EngineerDesk.state.historyRevision = null;}')
                await page.wait_for_function('() => EngineerDesk.state.a && document.getElementById("selection-summary").textContent === "Preparing selected comparison"')
                outline = await page.locator('#map path').first.get_attribute('d')
                import gt7_recording, gt7_telemetry
                packets = [(wall, gt7_telemetry.parse_packet(plain)) for wall, _, plain in gt7_recording.Recording(fixture.live_path).packets()]
                last_wall, last_packet = packets[-1]
                for wall, packet in packets[10:40]:
                    fixture.live.put(packet._replace(lap=last_packet.lap, last_ms=last_packet.last_ms), last_wall+wall-packets[0][0]+.1)
                assert fixture.live.flush(10)
                before_distance = fixture.live.snapshot()['current']['distance_m']
                await page.wait_for_function('(d) => EngineerDesk.state.detail?.distance_m >= d', arg=before_distance)
                assert await page.locator('#map path').first.get_attribute('d') == outline, 'growing prefix rescaled outline'
                async def outside_outline(route):
                    response = await route.fetch()
                    data = await response.json()
                    if data.get('lap', {}).get('state') == 'provisional':
                        data['lap']['trace'][-1]['x'] += 500
                        data['lap']['trace'][-1]['z'] += 500
                    await route.fulfill(json=data)
                await page.route('**/race-engineer/lap?*', outside_outline)
                await page.wait_for_function('() => EngineerDesk.state.detail?.trace?.at(-1)?.x > 300')
                assert await page.locator('#map path').first.get_attribute('d') == outline, 'live coordinate outside outline changed map bounds'
                await page.unroute('**/race-engineer/lap?*', outside_outline)
                await page.unroute('**/race-engineer/compare?*', pending_pair)
                await page.wait_for_function('() => EngineerDesk.state.pair?.state === "ready" && EngineerDesk.state.pair?.delta?.length > 0')
                assert await page.locator('#delta').inner_text() != 'Unavailable'
                assert await page.locator('#sectors tbody tr').first.locator('td').nth(1).inner_text() != 'Unavailable'
                # A detail can become ready between outline and compare reads.
                selected_a = await page.evaluate('() => EngineerDesk.state.a')
                missed = [False]
                async def recovering_a(route):
                    from urllib.parse import parse_qs, urlsplit
                    identity = parse_qs(urlsplit(route.request.url).query).get('id', [''])[0]
                    if identity == selected_a and not missed[0]:
                        missed[0] = True
                        await route.fulfill(json=dict(version=1, generation=fixture.live.generation, state='pending'))
                    else:
                        await route.continue_()
                await page.route('**/race-engineer/lap?*', recovering_a)
                await page.locator('#lap-a').select_option(selected_a)
                await page.wait_for_function('(id) => EngineerDesk.state.pair?.a?.id === id && !EngineerDesk.state.analysisBusy', arg=selected_a)
                assert missed[0]
                # Selected fast A must appear above the slower B, even if the
                # temporary map outline came from a slower completed lap.
                left_path = await page.locator('#chart path').nth(1).get_attribute('d')
                right_path = await page.locator('#chart path').nth(2).get_attribute('d')
                assert float(left_path.split('L')[0].split()[1]) < float(right_path.split('L')[0].split()[1]), 'ready comparison chart used the fallback lap instead of selected A'
                await page.unroute('**/race-engineer/lap?*', recovering_a)
                if '--shots' in sys.argv:
                    fixture.live.put(gt7_telemetry.parse_packet(raw)._replace(race_position=15, car_count=15), fixture.live._receipt[1]+.01)
                    assert fixture.live.flush(10)
                    await page.wait_for_function('() => document.getElementById("race-position").textContent === "P15/15"')
                    await page.locator('.mini-details summary').click()
                    # The fixture has no OBS: give the program still a synthetic frame.
                    await page.route('**/cockpit/program?*', lambda route: route.fulfill(
                        content_type='image/svg+xml', body=PROGRAM_SVG))
                    await page.wait_for_selector('#program-frame.loaded', timeout=8000)
                    for width,height,name in [(1440,1050,'desktop'),(1024,1100,'tablet'),(390,900,'phone')]:
                        await page.set_viewport_size({'width':width,'height':height})
                        await page.screenshot(path=str(ROOT/f'src/docs/wiki/images/console-race-engineer-{name}.png'),full_page=True)
                    await page.set_viewport_size({'width':1440,'height':1050})
                old=fixture.live.snapshot()['laps'][1]['id']
                await page.locator('#lap-b').select_option(old)
                assert not await page.locator('#follow').is_checked()
                await page.wait_for_function('(id) => EngineerDesk.state.detail?.id === id',arg=old)
                await page.locator('#zoom-from').fill('100')
                await asyncio.sleep(.7)
                assert await page.locator('#zoom-from').input_value() == '100', 'refresh overwrote focused distance input'
                await page.locator('#zoom-from').press('Tab')
                await page.locator('#zoom-to').fill('700');await page.locator('#zoom-to').press('Tab')
                assert await page.evaluate('() => EngineerDesk.state.zoom') == [100,700]
                await page.wait_for_selector('#map circle[data-live-car]')
                before_car = await page.locator('#map circle[data-live-car]').get_attribute('cx')
                async def moving_car(route):
                    response = await route.fetch(); data = await response.json()
                    data['instruments']['position'] = {'x': 0, 'z': 159.2}
                    await route.fulfill(json=data)
                await page.route('**/race-engineer/snapshot', moving_car)
                await page.wait_for_function('() => EngineerDesk.state.snapshot?.instruments?.position?.x === 0')
                assert await page.locator('#map circle[data-live-car]').get_attribute('cx') != before_car, 'snapshot position did not move the manual-view car'
                assert await page.evaluate('() => EngineerDesk.state.b') == old
                assert await page.locator('#map circle').count() == 1, 'selected trace endpoint was also drawn as a car'
                async def missing_car(route):
                    response = await route.fetch(); data = await response.json()
                    data['instruments']['position'] = None
                    data['instruments']['race_position'] = None
                    data['instruments']['car_count'] = None
                    await route.fulfill(json=data)
                await page.unroute('**/race-engineer/snapshot', moving_car)
                await page.route('**/race-engineer/snapshot', missing_car)
                await page.wait_for_function('() => EngineerDesk.state.snapshot?.instruments?.position === null')
                assert await page.locator('#map circle').count() == 0, 'missing position retained a car marker'
                assert await page.locator('#race-position').inner_text() == 'Unavailable', 'missing race position retained a place'
                await page.unroute('**/race-engineer/snapshot', missing_car)
                async def another_track(route):
                    response = await route.fetch(); data = await response.json()
                    data['current']['track_id'] = 'unrelated-track'
                    data['current']['session_id'] = 'unrelated-session'
                    await route.fulfill(json=data)
                await page.route('**/race-engineer/snapshot', another_track)
                await page.wait_for_function('() => EngineerDesk.state.snapshot?.current?.track_id === "unrelated-track"')
                assert await page.locator('#map circle').count() == 0, 'unrelated historical map retained a current car'
                await page.unroute('**/race-engineer/snapshot', another_track)
                await page.wait_for_function('() => document.getElementById("delta").textContent !== "Unavailable"')
                pair_entered, pair_release, pair_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
                async def held_manual_pair(route):
                    from urllib.parse import parse_qs, urlsplit
                    if parse_qs(urlsplit(route.request.url).query).get('b', [''])[0] == old:
                        response = await route.fetch(); data = await response.json()
                        pair_entered.set(); await pair_release.wait()
                        await route.fulfill(json=data); pair_done.set()
                    else:
                        await route.continue_()
                await page.route('**/race-engineer/compare?*', held_manual_pair)
                await asyncio.wait_for(pair_entered.wait(), timeout=5)
                live_delta = await page.locator('#delta').inner_text()
                assert live_delta != 'Unavailable', 'manual comparison had no live delta before snapshot'
                before_snapshot = await page.evaluate('() => EngineerDesk.state.received')
                await page.wait_for_function('(stamp) => EngineerDesk.state.received > stamp', arg=before_snapshot)
                assert await page.locator('#delta').inner_text() == live_delta, 'snapshot marker update cleared a pending manual live delta'
                pair_release.set(); await asyncio.wait_for(pair_done.wait(), timeout=5)
                await page.unroute('**/race-engineer/compare?*', held_manual_pair)
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
                revision = fixture.live.history(None)['revision']
                await other.wait_for_function('(revision) => EngineerDesk.state.historyRevision === revision', arg=revision)
                assert await other.evaluate('() => EngineerDesk.state.newLaps') == 0, 'following live accumulated a notice'
                await page.locator('#follow').check()
                assert await page.evaluate('() => EngineerDesk.state.newLaps') == 0, 'following live did not clear the notice'
                await page.locator('#follow').uncheck()
                fixture.feed_live()
                await page.wait_for_function('() => EngineerDesk.state.newLaps > 0')
                current_session = fixture.live.snapshot()['session_id']
                newest = fixture.live.snapshot()['laps'][-1]['id']
                await page.locator('#session').select_option(current_session)
                await page.locator('#lap-b').select_option(newest)
                assert await page.evaluate('() => EngineerDesk.state.newLaps') == 0, 'newest completed lap did not clear the notice'
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
                shared_label = '<img src=x onerror="window.injected=true">'
                assert shared_label in ' '.join(await page.locator('#session option').all_text_contents()), 'reference session lost its granted label'
                assert shared_label in ' '.join(await page.locator('#lap-a option').all_text_contents()), 'reference laps lost their granted label'
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

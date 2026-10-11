#!/usr/bin/env python3
"""Verify the approved Engineer layout with the real signed desk at three widths."""
import asyncio
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from engineer_ui_fixture import Fixture, gate

async def main():
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix='racecast-layout-') as root:
        fixture=Fixture(root);fixture.access().update(enabled=True,expected=0)
        server=fixture.console(with_live=True)
        from test_gt7_track_definitions import definition
        fixture.live._definitions.save(definition(),0);fixture.feed_live()
        try:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                page=await browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                token=gate.m.console_auth.mint_token(gate.SECRET,'engineer')
                await page.goto(f'http://127.0.0.1:{server.server_port}/console/race-engineer?t={token}')
                await page.wait_for_function('() => EngineerDesk.state.pair?.state === "ready"')
                first_detail=page.locator('#larger-sectors [data-section]').first.locator('details')
                await first_detail.locator('summary').click()
                for _ in range(3):
                    stamp=await page.evaluate('() => EngineerDesk.state.received')
                    await page.wait_for_function('(stamp)=>EngineerDesk.state.received>stamp&&!EngineerDesk.state.analysisBusy',arg=stamp)
                    assert await first_detail.evaluate('(el)=>el.open'), 'analysis refresh closed an expanded section'
                    assert await first_detail.locator('summary').evaluate('(el)=>el===document.activeElement'), 'analysis refresh lost disclosure keyboard focus'
                await first_detail.locator('summary').click()
                stamp=await page.evaluate('() => EngineerDesk.state.received')
                await page.wait_for_function('(stamp)=>EngineerDesk.state.received>stamp&&!EngineerDesk.state.analysisBusy',arg=stamp)
                assert not await first_detail.evaluate('(el)=>el.open'), 'analysis refresh reopened a collapsed section'
                for width,height in ((1440,1050),(1024,1100),(390,900)):
                    await page.set_viewport_size(dict(width=width,height=height))
                    assert await page.evaluate('() => document.documentElement.scrollWidth<=innerWidth'), 'approved layout overflowed'
                    for selector in ('#chart','#delta-chart','#map','#lap-a','#reference-source','#channel','#zoom-to','#selection-summary'):
                        assert await page.locator(selector).is_visible(), f'{width}: essential analysis hidden: {selector}'
                    assert await page.locator('#selection-summary').inner_text(), 'phone selected comparison had no feedback'
                    assert await page.locator('#larger-sectors [data-section]').count() == 3, 'reviewed sections were not grouped'
                    detail=page.locator('#larger-sectors [data-section]').first.locator('details')
                    await detail.locator('summary').click()
                    assert await detail.locator('table').is_visible(), 'section mini-sectors could not be expanded'
                    assert await detail.locator('tbody tr').count() > 0, 'section details did not include diagnostic segments'
                    groups=page.locator('#larger-sectors [data-section]')
                    for index, expected in enumerate((['1','2'],['2','3','4'],['4','5'])):
                        assert await groups.nth(index).locator('tbody tr td:first-child').all_text_contents() == expected, 'diagnostic disclosure included unrelated segments'

                    await detail.locator('summary').click()

                    positions=await page.evaluate('() => ["lap-time","delta","best","race-position"].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {x:r.x,y:r.y};})')
                    if width>600:
                        assert max(p['y'] for p in positions)-min(p['y'] for p in positions)<2, 'headline metrics were not one row'
                    else:
                        assert abs(positions[0]['y']-positions[1]['y'])<2 and abs(positions[2]['y']-positions[3]['y'])<2, 'phone headline did not use two rows'
                    assert await page.locator('#channel').evaluate('(el)=>!!el.closest(".plot-card")'), 'chart controls were separated from charts'
                    await page.locator('.mini-details summary').click()
                    assert await page.locator('#sectors').is_visible(), 'diagnostic sectors could not be expanded'
                    await page.locator('.mini-details summary').click()
                await page.evaluate('() => clearInterval(timer)')
                await page.wait_for_function('() => !EngineerDesk.state.busy && !EngineerDesk.state.analysisBusy')
                await page.evaluate('() => {state.savedLayoutPair=state.pair;state.savedLayoutDetail=state.detail;}')
                await first_detail.locator('summary').click()
                await page.evaluate('() => render()')
                assert await page.evaluate('() => state.sectionOpen.size') > 0
                await page.evaluate('() => {state.detail={...state.detail,definition_fingerprint:"new-definition"};render();}')
                assert not await first_detail.evaluate('(el)=>el.open'), 'new section definition inherited old disclosure state'
                await page.evaluate('() => {state.detail=state.savedLayoutDetail;render();}')
                await first_detail.locator('summary').click()
                await page.evaluate('() => render()')
                await page.evaluate('() => clearPlots()')
                assert await page.evaluate('() => state.sectionOpen.size') == 0, 'cleared selection retained section state'
                await page.evaluate('() => {state.detail=state.savedLayoutDetail;state.pair={...state.savedLayoutPair,delta:[]};render();}')

                assert 'Waiting for matching distance samples' in await page.locator('#selection-summary').inner_text(), 'empty provisional comparison claimed a measured distance'
                await page.evaluate('() => {state.pair={...state.savedLayoutPair,delta:state.savedLayoutPair.delta.slice(0,2)};render();}')
                expected_distance=await page.evaluate('() => state.pair.delta.at(-1).d.toFixed(0)')
                assert 'delta through '+expected_distance+' m' in await page.locator('#selection-summary').inner_text(), 'summary used the trace end instead of the calculated delta'
                await page.evaluate('() => {state.pair=null;render();}')
                assert 'Preparing selected comparison' in await page.locator('#selection-summary').inner_text(), 'pending comparison asked for a new reference'
                await page.evaluate('() => {state.detail={...state.detail,sectors:[.5]};render();}')
                partial=page.locator('#larger-sectors [data-section]').first.locator('details')
                await partial.locator('summary').click()
                assert await partial.locator('tbody tr').first.locator('td').nth(2).inner_text() == 'Not yet driven', 'unfinished diagnostic segment showed a complete time'
                await partial.locator('summary').click()
                await page.evaluate('() => {state.detail=state.savedLayoutDetail;render();}')

                await page.evaluate('() => {state.pair={...state.savedLayoutPair,compatible:false,delta:[]};render();}')
                assert 'outside the comparable pool' in await page.locator('#selection-summary').inner_text(), 'incompatible selection claimed a usable comparison'
                await page.evaluate('() => {state.pair=state.savedLayoutPair;state.detail=state.savedLayoutDetail;render();}')
                await page.evaluate('() => showDelta(-.25)')
                assert 'gain' in (await page.locator('#delta').get_attribute('class') or '') and 'loss' not in (await page.locator('#delta').get_attribute('class') or ''), 'negative delta lost gain emphasis'
                await page.evaluate('() => showDelta(.25)')
                assert 'loss' in (await page.locator('#delta').get_attribute('class') or '') and 'gain' not in (await page.locator('#delta').get_attribute('class') or ''), 'positive delta retained the previous gain'
                await page.evaluate('() => showDelta(0)')
                assert await page.locator('#delta').inner_text() == '0.000' and not (await page.locator('#delta').get_attribute('class') or ''), 'zero delta retained a gain or loss'
                for invalid in ('Infinity','-Infinity'):
                    await page.evaluate('() => showDelta('+invalid+')')
                    assert await page.locator('#delta').inner_text() == 'Unavailable' and not (await page.locator('#delta').get_attribute('class') or ''), 'nonfinite delta claimed a gain or loss'
                await page.evaluate('() => showDelta(null)')
                assert await page.locator('#delta').inner_text() == 'Unavailable' and not (await page.locator('#delta').get_attribute('class') or ''), 'missing delta retained gain/loss emphasis'
                await page.locator('#channel').select_option('brake')
                await page.locator('#zoom-from').fill('100');await page.locator('#zoom-to').fill('700');await page.locator('#zoom-to').dispatch_event('change')
                assert await page.evaluate('() => EngineerDesk.state.zoom') == [100,700], 'phone controls did not apply zoom'
                await page.locator('#zoom-reset').click()
                assert await page.evaluate('() => EngineerDesk.state.zoom') is None
                assert not errors,errors
                await browser.close()
        finally:
            server.shutdown();server.server_close();fixture.live.close()
    print('PASS approved Engineer layout: headline, controls, phone analysis and three widths')
    return None
if __name__=='__main__':asyncio.run(main())

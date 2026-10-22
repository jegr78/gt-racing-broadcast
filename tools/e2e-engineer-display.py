#!/usr/bin/env python3
"""Signed desk browser checks for empty, provisional and reviewed analysis display."""
import asyncio
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
from engineer_ui_fixture import Fixture, gate

async def main():
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix='racecast-display-') as root:
        fixture=Fixture(root); fixture.access().update(enabled=True,expected=0)
        server=fixture.console(with_live=True); fixture.feed_live()
        try:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                page=await browser.new_page(viewport={'width':1440,'height':1050})
                token=gate.m.console_auth.mint_token(gate.SECRET,'engineer')
                await page.goto(f'http://127.0.0.1:{server.server_port}/console/race-engineer?t={token}')
                await page.wait_for_function('() => EngineerDesk.state.pair?.state === "ready" && !EngineerDesk.state.analysisBusy')
                await page.evaluate('() => {clearInterval(timer);state.savedB=structuredClone(state.detail);state.savedPair=structuredClone(state.pair);}')
                await page.evaluate('() => instruments({...state.snapshot,current:{...state.snapshot.current,elapsed_s:125.678},best_s:62.345,theoretical_s:59.9996})')
                assert await page.locator('#lap-time').inner_text() == '2:05.678'
                assert await page.locator('#best').inner_text() == '1:02.345'
                assert await page.locator('#theory').inner_text() == '1:00.000', 'round milliseconds before splitting minutes'
                assert '1:02.345' in await page.evaluate('() => lapLabel({lap:2,state:"confirmed",time_s:62.345,comparison_eligible:true})')
                assert await page.evaluate('() => {const td=document.createElement("td");cellValue(td,4.125);return td.textContent;}') == '0:04.125'
                assert await page.evaluate('() => {const td=document.createElement("td");cellValue(td,-.125,false,"N/A",true);return td.textContent;}') == '-0.125'
                assert await page.evaluate('() => [0,.004,3599.9996,null,NaN,Infinity,-1].map(lapTime)') == [
                    '0:00.000','0:00.004','60:00.000','N/A','N/A','N/A','N/A']
                await page.evaluate('() => instruments(state.snapshot)')
                await page.evaluate('() => {state.savedLaps=state.laps;state.laps=[{session_id:"previous",comparison_eligible:true}];instruments({...state.snapshot,current:{...state.snapshot.current,session_id:"new"},best_s:null,theoretical_s:null});}')
                assert await page.locator('#best').inner_text() == 'No eligible lap', 'previous session hid the empty best state'
                assert await page.locator('#theory').inner_text() == 'No eligible lap', 'previous session hid the empty theoretical state'
                await page.evaluate('() => {state.laps=state.savedLaps;instruments(state.snapshot);}')

                await page.locator('.mini-details summary').click()
                context=await page.locator('#context').inner_text()
                assert '{}' not in context and 'Declared settings' not in context, 'empty JSON remained in context'
                body=await page.locator('body').inner_text()
                assert '512 summaries' not in body and 'cannot be recalled' not in body, 'internal limits remained on desk'
                assert 'Retained-detail' not in body and 'observed prefix only' not in body and 'data age' not in body, 'technical jargon remained'
                cell=page.locator('#sectors tbody tr').first.locator('td').nth(2)
                assert await cell.inner_text() == 'Not yet driven', 'future sector rendered as missing data'
                assert 'not-driven' in (await cell.get_attribute('class') or ''), 'future sector was not muted'
                assert await cell.evaluate('(el)=>getComputedStyle(el).color!==getComputedStyle(document.body).color'), 'future placeholder used the measured-value color'
                out=await page.evaluate('() => lapLabel({lap:0,state:"confirmed",time_s:null,comparison_eligible:false})')
                assert 'Out lap' in out and 'N/A' not in out and 'Lap 0' not in out, 'out lap read as missing timed lap'
                assert 'Out lap' in await page.evaluate('() => readableContext({lap:0,state:"confirmed"})'), 'out-lap context claimed a completed lap'
                await page.evaluate('() => graph("delta-chart",[],"delta",170,0)')
                empty=await page.locator('#delta-chart').text_content()
                assert 'No comparison' in empty and '0 m' not in empty, 'empty delta chart invented axes'
                await page.evaluate('() => graph("delta-chart",[{rows:[{d:0,delta:-1},{d:1000,delta:1}],color:"red"}],"delta",170,1000)')
                assert 's' in await page.locator('#delta-chart').text_content(), 'delta axis lost seconds'
                assert await page.locator('#delta-chart [data-zero]').count() == 1, 'delta chart lost zero line'
                zero=await page.locator('#delta-chart [data-zero]').get_attribute('d')
                curve=await page.locator('#delta-chart path').last.get_attribute('d')
                points=re.findall(r'(?:M|L)([\d.]+) ([\d.]+)',curve)
                middle=(float(points[0][1])+float(points[-1][1]))/2
                assert abs(float(zero.split()[1].split('H')[0])-middle)<.01, 'zero line did not cross zero delta' 
                # The display groups the 3 m tail with the preceding 200 m block.
                await page.evaluate('''() => {state.detail={...state.savedB,state:"confirmed",trace_complete:true,length_m:1003,trace:[{d:0,t:0,x:0,z:0},{d:1003,t:16,x:1,z:1}],sectors:[3,3,3,3,3,.02]};state.pair={compatible:true,b:state.detail,a:{...state.detail,id:"other",sectors:[3,3,3,3,3,.01]},delta:[],mini_sectors:{a:[3,3,3,3,3,.01],b:[3,3,3,3,3,.02],delta:[0,0,0,0,0,.01]}};state.a="other";render();}''')
                assert await page.locator('#sectors tbody tr').count() == 5, 'tiny trailing mini-sector remained separate'
                last=await page.locator('#sectors tbody tr').last.locator('td').all_text_contents()
                assert last == ['5–6','0:03.010','0:03.020','+0.010'], 'grouped tail lost formatted times or signed delta'
                await page.evaluate('() => {state.pair.mini_sectors.b[5]=null;render();}')
                assert await page.locator('#sectors tbody tr').last.locator('td').nth(2).inner_text() == 'N/A', 'missing tail produced a summed time'
                # Only matching reviewed definitions may show a larger-sector delta.
                await page.evaluate('''() => {const sections={reviewed:true,name:"Three race sections",names:["Opening","Middle","Closing"],bounds_m:[0,300,700,1003],times_s:[3,7,6],variant_id:"three"};state.detail={...state.detail,larger_sectors:sections,definition_fingerprint:"same",sector_variant_id:"three",corner_metrics:Array.from({length:8},(_,i)=>({number:i+1,apex_m:1003}))};state.pair={...state.pair,b:state.detail,a:{...state.detail,id:state.a,larger_sectors:{...sections,times_s:[4,6,6]}},definition_compatible:true};render();}''')
                table=page.locator('#larger-sectors')
                assert await table.locator('[data-section]').count() == 3, 'reviewed sections were not structured'
                text=await table.inner_text()
                assert 'Opening' in text and '-1.000' in text and '1.000' in text, 'reviewed sections lost gain/loss'
                assert 'A 0:04.000 · B 0:03.000' in text, 'reviewed section times lost the lap-clock format'
                boxes=await page.locator('#map text[data-turn]').evaluate_all('(nodes) => nodes.map(n=>{const r=n.getBBox();return {x:r.x,y:r.y,w:r.width,h:r.height};})')
                assert len(boxes) == 8, 'adjacent turn labels disappeared'
                assert all(a['x']+a['w']<=b['x'] or b['x']+b['w']<=a['x'] or a['y']+a['h']<=b['y'] or b['y']+b['h']<=a['y'] for i,a in enumerate(boxes) for b in boxes[i+1:]), 'turn labels overlapped'
                for mutation, restore in [
                    ('state.pair.a.larger_sectors.reviewed=false', 'state.pair.a.larger_sectors.reviewed=true'),
                    ('state.pair.a.larger_sectors.variant_id="other"', 'state.pair.a.larger_sectors.variant_id="three"'),
                    ('state.pair.a.larger_sectors.bounds_m=[0,301,700,1003]', 'state.pair.a.larger_sectors.bounds_m=[0,300,700,1003]'),
                    ('state.pair.a.larger_sectors.bounds_m=[0,300,700,1003,1100]', 'state.pair.a.larger_sectors.bounds_m=[0,300,700,1003]'),
                    ('state.pair.compatible=false', 'state.pair.compatible=true'),
                    ('state.pair.a.larger_sectors.times_s=[null,6,6]', 'state.pair.a.larger_sectors.times_s=[4,6,6]')]:
                    await page.evaluate('() => {'+mutation+';render();}')
                    assert '-1.000' not in await page.locator('#larger-sectors').inner_text(), 'unmatched reference sections claimed a delta'
                    if 'times_s=[null' in mutation:
                        assert await table.locator('[data-section]').first.locator('.section-delta').inner_text() == 'N/A', 'missing section time produced a delta'

                    await page.evaluate('() => {'+restore+';render();}')
                await page.evaluate('() => {state.pair.definition_compatible=false;render();}')
                assert '-1.000' not in await page.locator('#larger-sectors').inner_text(), 'mismatched sections claimed a delta'
                await page.evaluate('() => {state.detail.larger_sectors.reviewed=false;render();}')
                assert 'Unconfirmed' in await page.locator('#larger-sectors').inner_text(), 'unreviewed sections claimed confirmation'
                assert await page.locator('#larger-sectors table').count() == 0, 'unreviewed sections rendered confirmed timing rows'
                await page.evaluate('''() => {state.detail={...state.savedB,context_settings:{bop:true,fuel_x:3},strategy:{fuel_map:2,targets:{"3":6500},shortshift:"<img src=x onerror=alert(1)>"}};state.pair=null;render();}''')
                named=await page.locator('#context').inner_text()
                assert 'BoP' in named and '3' in named and 'Fuel map' in named and 'Shift target G3: 6500 RPM' in named, 'declared values were not named'
                assert await page.locator('#context img').count() == 0, 'declared context became HTML'
                await browser.close()
        finally:
            server.shutdown();server.server_close();fixture.live.close()
    print('PASS Engineer desk display cleanup: all eight items')
    return None

if __name__=='__main__':asyncio.run(main())

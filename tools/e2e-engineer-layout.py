#!/usr/bin/env python3
"""Verify the approved Engineer layout with the real signed desk at three widths."""
import asyncio
import re
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
                    lines=await page.evaluate('() => [...document.getElementById("selection-summary").closest("section").querySelectorAll("p")].filter(p=>p.offsetParent&&p.textContent.trim()).map(p=>p.textContent)')
                    assert len(lines)==1, f'{width}: comparison feedback was split over several lines: {lines}'
                    assert not any(word in lines[0].split() for word in ('ready','pending','provisional','confirmed')), f'feedback exposed raw analysis states: {lines[0]}'
                    await page.evaluate('() => new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    for chart in ('#chart','#delta-chart'):
                        height=await page.evaluate('(sel)=>document.querySelector(sel+" text").getBoundingClientRect().height',chart)
                        assert height>=10, f'{width}: {chart} axis text rendered {height:.1f} px high'
                    taps=await page.locator('#larger-sectors summary, .mini-details summary').evaluate_all('(nodes)=>nodes.map(n=>n.getBoundingClientRect().height)')
                    assert width>600 or min(taps)>=44, f'phone disclosure tap targets too small: {taps}'
                    cards=await page.evaluate('''() => {const B=state.detail,bounds=B.larger_sectors.bounds_m,observed=B.trace.at(-1).d;return [...document.querySelectorAll('#larger-sectors [data-section]')].map((card,i)=>({text:card.innerText,expected:B.state!=='provisional'||bounds[i+1]<=observed?null:bounds[i]<=observed?'Still driving':'Not reached',range:Math.round(bounds[i])+'–'+Math.round(bounds[i+1])+' m'}));}''')
                    for card in cards:
                        assert card['range'] in card['text'], f'section card lacked its distance range: {card}'
                        assert re.search(r'\d+ diagnostic mini-sectors',card['text']), f'section card lacked its mini-sector count: {card}'
                        if card['expected']:
                            assert card['expected'] in card['text'] and 'Not yet driven' not in card['text'], f'section progress was not distinguished: {card}'
                    assert re.fullmatch(r'Against A · through \d+ m',await page.locator('#delta + small').inner_text()), 'live delta lacked its reference distance'
                    assert re.fullmatch(r'Lap \d+ · this session',await page.locator('#best + small').inner_text()), 'best lap lacked its lap number'
                    assert await page.locator('#larger-sectors [data-section]').count() == 3, 'reviewed sections were not grouped'
                    detail=page.locator('#larger-sectors [data-section]').first.locator('details')
                    await detail.locator('summary').click()
                    assert await detail.locator('table').is_visible(), 'section mini-sectors could not be expanded'
                    assert await detail.locator('tbody tr').count() > 0, 'section details did not include diagnostic segments'
                    groups=page.locator('#larger-sectors [data-section]')
                    for index, expected in enumerate((['1','2'],['2','3','4'],['4','5'])):
                        assert await groups.nth(index).locator('tbody tr td:first-child').all_text_contents() == expected, 'diagnostic disclosure included unrelated segments'

                    await detail.locator('summary').click()

                    if width==390:
                        await page.locator('#best').evaluate('(el)=>{el.previousElementSibling.replaceChildren(document.createTextNode("Best"),document.createElement("br"),document.createTextNode("completed · s"));}')
                    positions=await page.evaluate('() => ["lap-time","delta","best","race-position"].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {x:r.x,y:r.y};})')
                    # The program still sits beside the headline only where four tiles keep
                    # their width; narrower, it stacks below so the labels never wrap.
                    program=await page.evaluate('() => {const p=document.getElementById("program-card").getBoundingClientRect(),h=document.getElementById("instruments").getBoundingClientRect();return {beside:p.left>=h.right,below:p.top>=h.bottom};}')
                    assert program['beside'] if width>1100 else program['below'], f'{width}: program still placement {program}'
                    if width>600:
                        assert max(p['y'] for p in positions)-min(p['y'] for p in positions)<2, 'headline metrics were not one row'
                        assert all(a['x']<b['x'] for a,b in zip(positions,positions[1:],strict=False)), 'headline metric order differs from the approved draft'
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
                assert await page.locator('#delta').inner_text() == '+0.250', 'positive delta relied on color alone'
                signed=await page.evaluate('() => {const td=document.createElement("td");cellValue(td,.5,false,"Unavailable",true);return td.textContent;}')
                assert signed == '+0.500', 'positive sector difference relied on color alone'
                assert 'loss' in (await page.locator('#delta').get_attribute('class') or '') and 'gain' not in (await page.locator('#delta').get_attribute('class') or ''), 'positive delta retained the previous gain'
                await page.evaluate('() => showDelta(0)')
                assert await page.locator('#delta').inner_text() == '0.000' and not (await page.locator('#delta').get_attribute('class') or ''), 'zero delta retained a gain or loss'
                for invalid in ('Infinity','-Infinity'):
                    await page.evaluate('() => showDelta('+invalid+')')
                    assert await page.locator('#delta').inner_text() == 'Unavailable' and not (await page.locator('#delta').get_attribute('class') or ''), 'nonfinite delta claimed a gain or loss'
                await page.evaluate('() => showDelta(null)')
                assert await page.locator('#delta').inner_text() == 'Unavailable' and not (await page.locator('#delta').get_attribute('class') or ''), 'missing delta retained gain/loss emphasis'
                observed=await page.evaluate('() => state.detail.trace.at(-1).d')
                ahead=await page.evaluate('(d) => (state.detail.corner_metrics||[]).filter(c=>c.apex_m>d).map(c=>"T"+c.number)',observed)
                labels=await page.locator('#map [data-turn]').all_text_contents()
                assert not ahead or set(ahead)&set(labels), f'turns ahead of the car had no map label: ahead {ahead}, labels {labels}'
                await page.evaluate('() => {state.snapshot={...state.snapshot,receipt:{...state.snapshot.receipt,packet_received_at:1700000000},replay:{active:true,label:"Recorded session",speed:4}};state.received=performance.now();age();}')
                assert await page.locator('#receipt').inner_text() == 'Recorded: 22:13:20 UTC · last packet: '+(await page.evaluate('() => num(state.snapshot.receipt.packet_age_s,1)'))+' s', 'replay receipt did not use the UTC recorded time'
                await page.evaluate('() => {state.snapshot={...state.snapshot,replay:{active:false}};age();}')
                await page.evaluate('() => {state.snapshot={...state.snapshot,receipt:{...state.snapshot.receipt,packet_received_at:new Date(2023,0,1,0,5,3).getTime()/1000}};age();}')
                assert (await page.locator('#receipt').inner_text()).startswith('Receipt: 00:05:03 · '), 'live receipt was not a 24-hour time: '+await page.locator('#receipt').inner_text()
                await page.evaluate('() => {state.generation="g1";state.snapshot={...state.snapshot,replay:{active:false}};state.refSource="ref";state.a="r:ref:1:2";resetGeneration({generation:"g2",replay:{active:true}});}')
                assert (await page.locator('#notice').inner_text()).startswith('Replay started.'), 'replay start was reported as something else'
                assert await page.evaluate('() => [state.refSource,state.a]') == ['ref','r:ref:1:2'], 'a source switch cleared the shared reference'
                await page.evaluate('() => {state.snapshot={...state.snapshot,replay:{active:true}};resetGeneration({generation:"g3",replay:{active:false,live_gap:true}});}')
                assert (await page.locator('#notice').inner_text()).startswith('Replay ended.'), 'replay end was reported as a relay restart'
                await page.evaluate('() => {state.snapshot={...state.snapshot,replay:{active:true}};resetGeneration({generation:"g4",replay:{active:true}});}')
                assert (await page.locator('#notice').inner_text()).startswith('A different replay started.'), 'a replay switch was not named'
                await page.evaluate('() => {state.snapshot={...state.snapshot,replay:{active:false}};state.a="live-lap";resetGeneration({generation:"g5",replay:{active:false}});}')
                assert (await page.locator('#notice').inner_text()).startswith('Relay restarted.') and await page.evaluate('() => state.a') == '', 'a relay restart kept a live reference'
                await page.evaluate('() => instruments({instruments:{},current:{lap:3,track:null,car:"Car"},track_recognition:false})')
                assert all(await page.locator('[data-unit-for]').evaluate_all('(nodes)=>nodes.map(n=>n.hidden)')), 'units stayed beside unavailable values'
                assert [await page.locator(t).inner_text() for t in ('#tyres-front','#tyres-rear')] == ['Unavailable','Unavailable'], 'unavailable tyre pairs repeated the placeholder'
                assert (await page.locator('#source-state').inner_text()).startswith('Unknown track · track recognition data not loaded'), 'missing recognition data was not named'
                await page.evaluate('() => instruments({instruments:{speed_kmh:120,rpm:5000,throttle:40,brake:0,tyres_c:[80,null,null,null]},current:{lap:3,track:null,car:"Car"},track_recognition:true})')
                assert not any(await page.locator('[data-unit-for]').evaluate_all('(nodes)=>nodes.map(n=>n.hidden)')), 'units were hidden beside measured values'
                assert await page.locator('#tyres-front').inner_text() == '80 / Unavailable', 'a partly measured tyre pair lost its value'
                assert (await page.locator('#source-state').inner_text()).startswith('Unknown track · Car'), 'recognisable data still blamed the track data'
                await page.evaluate('() => clearPlots()')
                assert not await page.locator('#larger-sectors').is_visible(), 'an empty section card stayed visible'
                await page.evaluate('() => {state.pair=state.savedLayoutPair;state.detail=state.savedLayoutDetail;render();}')
                picks=await page.evaluate('''() => {const saved={laps:state.laps,snapshot:state.snapshot,a:state.a,b:state.b,follow:state.follow,refSource:state.refSource},cur=state.snapshot.current,lap={...state.snapshot.current,state:'confirmed',comparison_eligible:true},pick=()=>{state.a='';selections();return state.a;};
                 state.refSource='';state.follow=true;state.laps=[{...lap,id:'unknown',track_id:null,session_id:cur.session_id,time_s:1},{...lap,id:'known',track_id:'layout',session_id:cur.session_id,time_s:2}];
                 state.snapshot={...saved.snapshot,current:{...cur,track_id:'layout'}};const known=pick();
                 state.snapshot={...saved.snapshot,current:{...cur,track_id:null}};const unknown=pick();
                 state.rejected.add('unknown|'+cur.id);const rejected=pick();state.rejected.clear();
                 state.snapshot={...saved.snapshot,current:{...cur,track_id:'layout',provisional_comparison_eligible:false}};const closed=pick();
                 Object.assign(state,saved);state.rejected.clear();selections();return {known,unknown,rejected,closed};}''')
                assert picks == {'known':'known','unknown':'unknown','rejected':'','closed':''}, f'automatic reference ignored the comparable pool: {picks}'
                summary=await page.evaluate('''() => {state.note='';state.detail={...state.savedLayoutDetail,state:'provisional',provisional_comparison_eligible:false};state.pair=null;render();const text=$('selection-summary').textContent;state.detail=state.savedLayoutDetail;state.pair=state.savedLayoutPair;render();return text;}''')
                assert summary.startswith('Lap B is not comparable yet'), f'an ineligible lap B blamed the reference: {summary}'
                summary=await page.evaluate('''() => {state.note='';state.detail={...state.savedLayoutDetail,state:'confirmed',comparison_eligible:false};state.pair=null;render();const text=$('selection-summary').textContent;state.detail=state.savedLayoutDetail;state.pair=state.savedLayoutPair;render();return text;}''')
                assert summary.startswith('Lap B is outside the comparable pool'), f'an excluded completed lap B was reported as pending: {summary}'
                outcomes=await page.evaluate('''async () => {const snap0=state.snapshot;state.snapshot={...snap0,track_recognition:true};const saved={request:window.request,a:state.a,b:state.b,follow:state.follow,detail:state.detail,pair:state.pair,note:state.note,refSource:state.refSource,outline:state.outline},base=state.savedLayoutDetail,A={...base,id:'r:ref:1:4',state:'confirmed',track_id:'layout',comparison_eligible:true},run=async B=>{window.request=async(route)=>route==='/race-engineer/lap'?{state:'ready',lap:B}:route==='/race-engineer/compare'?{state:'ready',compatible:false,a:A,b:B,delta:[],warnings:[]}:{};state.rejected.clear();state.note='';state.refSource='';state.follow=true;state.a=A.id;state.b=B.id;state.outline=null;await selection();return {a:state.a,rejected:[...state.rejected],summary:$('selection-summary').textContent};};
                 const unknown={...base,id:'b4',session_id:'fresh-session',state:'provisional',track_id:null,provisional_comparison_eligible:true},laps=state.laps,snap=state.snapshot;
                 state.laps=[{...base,id:'closed',lap:1,state:'confirmed',session_id:unknown.session_id}];const unrecognised=await run(unknown);state.laps=laps;
                 state.snapshot={...snap,track_recognition:false};const noData=await run({...unknown,id:'b5'});state.snapshot=snap;
                 const out={unrecognised,noData,pending:await run({...base,id:'b1',session_id:'fresh-session',state:'provisional',track_id:null,provisional_comparison_eligible:true}),outlap:await run({...base,id:'b2',state:'provisional',track_id:'layout',provisional_comparison_eligible:false}),conflict:await run({...base,id:'b3',state:'provisional',track_id:'layout',provisional_comparison_eligible:true})};
                 window.request=saved.request;state.rejected.clear();Object.assign(state,{a:saved.a,b:saved.b,follow:saved.follow,detail:saved.detail,pair:saved.pair,note:saved.note,refSource:saved.refSource,outline:saved.outline});state.snapshot=snap0;render();return out;}''')
                assert outcomes['pending']['a'] == 'r:ref:1:4' and not outcomes['pending']['rejected'] and outcomes['pending']['summary'].startswith('Lap B is not comparable yet: the track'), f'an unrecognised first lap discarded the reference: {outcomes["pending"]}'
                assert outcomes['outlap']['a'] == 'r:ref:1:4' and not outcomes['outlap']['rejected'] and outcomes['outlap']['summary'].startswith('Lap B is not comparable yet: out lap'), f'an out lap discarded the reference: {outcomes["outlap"]}'
                assert outcomes['unrecognised']['a'] == '' and outcomes['noData']['a'] == '', f'a lap whose track will never be recognised kept promising recognition: {outcomes}'
                assert outcomes['conflict']['a'] == '' and outcomes['conflict']['rejected'] == ['r:ref:1:4|b3'], f'a real pool conflict kept or re-offered the reference: {outcomes["conflict"]}'
                label=await page.evaluate('''() => {const saved={refs:state.refs,refLaps:state.refLaps,refSource:state.refSource},base={...state.snapshot.current,state:'confirmed',comparison_eligible:true,session_id:'r:ref:1'};state.refs=[{id:'ref',label:'Morning'}];state.refSource='ref';state.refLaps=[{...base,id:'r:ref:1:0',lap:0,track:'',track_id:null},{...base,id:'r:ref:1:1',lap:1,track:'Ring',track_id:'ring'}];selections();const text=[...$('session').options].map(o=>o.text).find(t=>t.startsWith('Morning'));Object.assign(state,saved);selections();return text;}''')
                assert label and ' · Ring · ' in label, f'a shared session was named after its untracked out lap: {label}'
                counts=await page.evaluate('''() => {const count=()=>[...document.querySelectorAll('#larger-sectors [data-section] summary')].map(n=>n.textContent);state.note='';state.pair=state.savedLayoutPair;state.detail=state.savedLayoutDetail;render();const full=count(),rows=$('sectors').tBodies[0].rows.length,outline=state.outline;state.outline=state.savedLayoutPair.a;state.pair=null;state.detail={...state.savedLayoutDetail,state:'provisional',sectors:state.savedLayoutDetail.sectors.slice(0,1),trace:state.savedLayoutDetail.trace.filter(p=>p.d<=200)};render();const partial=count(),partialRows=$('sectors').tBodies[0].rows.length,future=$('sectors').tBodies[0].rows[partialRows-1]?.cells[2].textContent;state.outline=outline;state.pair=state.savedLayoutPair;state.detail=state.savedLayoutDetail;render();return {full,partial,rows,partialRows,future};}''')
                assert counts['partial'] == counts['full'] and counts['partialRows'] == counts['rows'], f'mini-sector counts followed the driven distance: {counts}'
                assert counts['future'] == 'Not yet driven', f'an undriven mini-sector was not marked: {counts}'
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

#!/usr/bin/env python3
"""Real-browser recording selection against synthetic files and production callbacks."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
import test_racecast as fixture
import test_ui_server as web


def open_recording_menu(page):
    page.get_by_role('button',name='Analysis',exact=True).click()
    if page.locator('#tm-recording-menu').get_attribute('open') is None:
        page.locator('#tm-recording-menu summary').click()


def main():
    from playwright.sync_api import sync_playwright
    with fixture._telemetry_sandbox() as (root,tgl):
        old=tgl.write_circle_recording(root)
        new=tgl.write_circle_recording(root,lap_secs=(20.,19.,18.),t0=1_700_007_200.)
        fixture.m.telemetry_laps_data(rec=fixture._stem(old))
        fixture.m.telemetry_laps_data(rec=fixture._stem(new))
        ctx=web._ctx()
        ctx['version']='dev'
        ctx['profiles']=lambda:dict(ok=True,active='solo',profiles=[dict(name='solo',kind='solo',template='pov')])
        ctx['telemetry_recordings']=fixture.m.telemetry_recordings_data
        ctx['telemetry_laps']=fixture.m.telemetry_laps_data
        ctx['telemetry_lap']=fixture.m.telemetry_lap_data
        ctx['telemetry_context_read']=fixture.m.telemetry_context_read_data
        ctx['telemetry_context_write']=fixture.m.telemetry_context_write_data
        primary=fixture.m.telemetry_lap_data(fixture._stem(new),'1','3')['lap']
        reference=fixture.m.telemetry_lap_data(fixture._stem(old),'1','2')['lap']
        def target(lap):
            return {k:lap[k] for k in ('rec','session','lap','recording_id')}
        report=dict(language='en',findings=[dict(priority=1,title='Saved comparison',
            telemetry=target(primary),comparisons=[target(reference)],evidence=[])],exercises=[],limitations=[])
        selected_recordings=[]
        def ai(operation,payload):
            if operation=='settings':return dict(ok=True,enabled=True,agents=[])
            if operation=='selection':
                selected_recordings.append(payload['rec'])
                return dict(ok=True,profile='solo',rec=payload['rec'],sessions=[])
            if operation=='history':return dict(ok=True,profile='solo',runs=[dict(id='saved',created_at=1700000000,state='completed')])
            if operation=='job':return dict(ok=True,profile='solo',state='completed',report=report)
            return dict(ok=True,busy=False)
        ctx['ai_request']=ai
        server,port=web._serve(ctx)
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.goto('http://127.0.0.1:'+str(port)+'/telemetry#telemetry')
                page.wait_for_function('() => tmState.profile === "solo" && tmState.lapB?.recording_id && currentView === "telemetry"')
                assert page.locator('#tm-b optgroup').count()==1,'default must use only selected recording'
                assert page.locator('#tm-b optgroup').first.get_attribute('label').startswith('Wed 15.11.'),'readable date label'
                page.locator('#tm-recording-menu summary').click()
                assert page.locator('#tm-recording-options input').count()==2
                page.get_by_role('button',name='All recordings',exact=True).click()
                page.wait_for_function('() => tmState.pool?.laps.length === 5')
                assert page.locator('#tm-b optgroup').count()==2
                assert 'overall best' in page.locator('#tm-b').inner_text()
                assert page.locator('#tm-b optgroup').first.locator('option').count()==2
                numbers=[int(text.split()[1]) for text in page.locator('#tm-b optgroup').first.locator('option').all_text_contents()]
                assert numbers==sorted(numbers),'laps must remain in driving order'
                page.locator('#tm-recording-options input').first.uncheck()
                page.wait_for_function('() => tmState.lapB && tmState.lapB.rec !== tmState.rec')
                page.evaluate('() => tcOpen()')
                page.wait_for_function('() => tc.doc && !tc.loading')
                assert page.evaluate('() => tc.target === tmState.lapB.rec'), 'Notes must follow analysed lap B, including cross-recording comparisons'
                page.evaluate('() => tcAddNote()')
                assert page.evaluate('() => tc.data.notes.at(-1).scope === "lap" && tc.data.notes.at(-1).lap === tmState.lapB.lap')
                page.evaluate('() => {tc.data.notes.pop();tc.dirty=false;return tcClose();}')
                page.evaluate('() => RacecastAI.selection()')
                assert selected_recordings[-1] == page.evaluate('() => tmState.lapB.rec'), 'AI selection must follow analysed lap B'
                assert page.locator('#tm-b optgroup').count()==1
                assert page.evaluate('() => tmState.pool.laps.every(l=>tmState.included.has(l.rec))')
                open_recording_menu(page)
                page.get_by_role('button',name='Only this recording',exact=True).click()
                page.wait_for_function('() => tmState.pool?.laps.length === 2')
                assert page.locator('#tm-b optgroup').count()==1
                assert page.evaluate('() => tmState.pool.theoretical_best')==18
                page.locator('#tm-recording-options input:checked').uncheck()
                page.wait_for_function('() => tmState.pool && tmState.pool?.laps.length === 0')
                assert page.locator('#tm-a option').inner_text()=='no other lap to compare'
                assert page.locator('#tm-b option').inner_text()=='no included lap'
                page.wait_for_function('() => !tmState.lapA && !tmState.lapB')
                open_recording_menu(page)
                page.get_by_role('button',name='Only this recording',exact=True).click()
                page.wait_for_function('() => tmState.lapB')
                page.evaluate('() => showView("reports")')
                page.evaluate('() => RacecastAI.history()')
                page.evaluate('() => showView("reports")')
                page.locator('#ai-history button').first.click()
                page.evaluate('() => showView("reports")')
                page.locator('#ai-report').get_by_role('button',name='Compare with S1 lap 2').click()
                page.wait_for_function('''() => document.getElementById('tm-err').textContent.includes('unavailable')
                    || (tmState.lapA && tmState.lapB && tmState.lapA.rec !== tmState.lapB.rec)''')
                assert page.evaluate('() => tmState.lapA.rec !== tmState.lapB.rec'),'saved cross-recording comparison lost'
                previous=page.evaluate('() => [tmState.a,tmState.b,[...tmState.included].sort()]')
                page.reload();page.wait_for_function('() => tmState.loaded && !!tmState.lapB && currentView === "telemetry"')
                page.get_by_role('button',name='Archive',exact=True).click()
                page.locator('#workspace-last').click()
                page.wait_for_function('() => !!tmState.lapA && tmState.lapA.rec !== tmState.lapB.rec')
                assert page.evaluate('() => [tmState.a,tmState.b,[...tmState.included].sort()]')==previous,'last comparison changed source or recording subset after reload'
                assert [page.locator('#tm-a').input_value(),page.locator('#tm-b').input_value()]==previous[:2], 'restored selectors differ from the loaded comparison'
                page.evaluate("""async () => {
                    window.savedComparison=workspaceSaved();
                    const changed={...window.savedComparison,reference:{...window.savedComparison.reference,recording_id:'0'.repeat(32)}};
                    localStorage.setItem('racecast-telemetry-comparison:'+tmState.profile,JSON.stringify(changed));
                    await workspaceLast();
                }""")
                assert page.evaluate('() => !tmState.lapA && document.getElementById("tm-err").textContent.includes("unavailable or changed")'), 'Last comparison must not substitute a changed reference'
                page.evaluate("""async () => {
                    localStorage.setItem('racecast-telemetry-comparison:'+tmState.profile,JSON.stringify({...window.savedComparison,reference:null}));
                    await workspaceLast();
                }""")
                assert page.evaluate('() => !tmState.lapA'), 'Last comparison must preserve an explicit absent reference'
                page.evaluate("""async () => {
                    localStorage.setItem('racecast-telemetry-comparison:'+tmState.profile,JSON.stringify(window.savedComparison));
                    await workspaceLast();delete window.savedComparison;
                }""")
                open_recording_menu(page)
                page.get_by_role('button',name='Only this recording',exact=True).click()
                page.wait_for_function('() => tmState.lapB && tmState.included.size === 1')
                report['findings'][0]['comparisons'][0]['recording_id']='0'*32
                page.evaluate('() => showView("reports")')
                page.locator('#ai-history button').first.click()
                page.evaluate('() => showView("reports")')
                page.locator('#ai-report').get_by_role('button',name='Compare with S1 lap 2').click()
                page.wait_for_function('() => document.getElementById("tm-err").textContent.includes("Comparison recording identity changed") || tmState.included.size > 1')
                assert page.evaluate('() => tmState.included.size === 1 && tmState.lapA.rec === tmState.lapB.rec'),'replaced reference expanded selection'
                import gt7_context
                for path,compound in ((old,'RH'),(new,'RM')):
                    gt7_context.Store.for_recording(path).save(dict(notes=[],sessions={'1':dict(
                        confirmed=True,settings={},stints=[dict(id='first',start_lap=1,compound=compound,
                        confirmed=True,tyre_service=False,warmup_laps=0)])}),expected=0)
                page.evaluate('() => tmDropLaps()')
                page.locator('#tm-compare-all').check()
                page.get_by_role('button',name='All recordings',exact=True).click()
                page.wait_for_function('() => tmState.pool?.laps.length === 5')
                page.locator('#tm-b').select_option(fixture._stem(old)+'|1|2')
                page.wait_for_function('() => tmState.lapB?.compound === "RH"')
                page.locator('#tm-compare-all').uncheck()
                page.wait_for_function('() => tmState.pool?.laps.length === 3')
                page.evaluate("""() => {
                    window.subsetOriginal=tmReloadRecordingPool;window.subsetSettled=false;
                    tmReloadRecordingPool=async()=>{try{return await window.subsetOriginal();}
                        finally{window.subsetSettled=true;}};
                }""")
                open_recording_menu(page)
                page.get_by_role('button',name='Only this recording',exact=True).click()
                page.wait_for_function('() => window.subsetSettled')
                page.evaluate("""() => {
                    tmReloadRecordingPool=window.subsetOriginal;
                    delete window.subsetOriginal;delete window.subsetSettled;
                }""")
                assert page.evaluate('() => tmState.lapB?.compound === "RM"'),'excluded B conditions blocked the included recording'
                page.locator('#tm-recording-options input').nth(1).check()
                page.wait_for_function('() => tmState.pool?.laps.length === 2')
                page.locator('#tm-recording-options input').first.uncheck()
                page.wait_for_function('() => tmState.lapB?.compound === "RH"')
                assert page.locator('#tm-b optgroup').count()==1
                assert page.evaluate('() => tmState.pool.theoretical_best')==16
                print('PASS browser: recording selection, saved references and condition changes')
                browser.close()
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__':main()

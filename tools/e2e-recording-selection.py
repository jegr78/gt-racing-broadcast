#!/usr/bin/env python3
"""Real-browser recording selection against synthetic files and production callbacks."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
import test_racecast as fixture
import test_ui_server as web


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
        server,port=web._serve(ctx)
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.goto('http://127.0.0.1:'+str(port))
                page.wait_for_function('() => tmState.profile === "solo"')
                page.evaluate('() => showView("telemetry")')
                page.wait_for_function('() => tmState.lapB')
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
                assert page.locator('#tm-b optgroup').count()==1
                assert page.evaluate('() => tmState.pool.laps.every(l=>tmState.included.has(l.rec))')
                page.get_by_role('button',name='Only this recording',exact=True).click()
                page.wait_for_function('() => tmState.pool?.laps.length === 2')
                assert page.locator('#tm-b optgroup').count()==1
                assert page.evaluate('() => tmState.pool.theoretical_best')==18
                page.locator('#tm-recording-options input:checked').uncheck()
                page.wait_for_function('() => tmState.pool && tmState.pool?.laps.length === 0')
                assert page.locator('#tm-a option').inner_text()=='no other lap to compare'
                assert page.locator('#tm-b option').inner_text()=='no included lap'
                page.wait_for_function('() => !tmState.lapA && !tmState.lapB')
                print('PASS browser: default, grouped labels/order/bests, all/only, empty selection')
                browser.close()
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__':main()

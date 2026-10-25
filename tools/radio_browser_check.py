#!/usr/bin/env python3
"""Synthetic browser acceptance for shared driver radio views and producer setup."""
import sys
import traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def check(browser,outdir,shots=None):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_driver_radio_ui import RadioFixture,console
    import e2e_visual as V
    out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
    results=[]
    def shot(page,name,viewport='desktop',wiki=None):
        path=out/(name+'-'+viewport+'.png');page.screenshot(path=str(path),full_page=True)
        results.append(V.SurfaceResult(name,viewport,path.name,[],0,None))
        if shots and wiki:
            import shutil
            Path(shots).mkdir(parents=True,exist_ok=True);shutil.copyfile(path,Path(shots)/wiki)
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.route('https://fonts.googleapis.com/**',lambda route:route.fulfill(status=200,content_type='text/css',body='/* Offline synthetic fixture. */'))
    try:
        with RadioFixture() as f:
            first=context.new_page();second=context.new_page();errors=[]
            first.on('pageerror',lambda error:errors.append(str(error)))
            first.goto(f.url+'/console/driver-radio?t='+f.token)
            second.goto(f.url+'/console/driver-radio')
            a=first.locator('#driver-radio');b=second.locator('#driver-radio')
            a.locator('[data-preset=fuel]').wait_for(state='visible')
            a.locator('[data-preset=fuel]').click();b.locator('[data-preset=fuel]').click()
            first.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('latest').textContent.includes('Fuel')")
            second.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('latest').textContent.includes('Fuel')")
            assert len(f.f.service.history()['messages'])==1,'views multiplied shared radio requests'
            f.enricher.step('audio')
            a.locator('#race-mode').select_option('laps');a.locator('#race-length').fill('37')
            a.locator('[data-preset=fuel]').click();first.wait_for_timeout(1100)
            assert a.locator('#race-length').input_value()=='37','preset request discarded an unsaved setup draft'
            # A successful save must not discard edits made while awaiting its response.
            first.evaluate("""window.__realRadioFetch=window.fetch;window.fetch=async(...args)=>{
                const response=await window.__realRadioFetch(...args);
                if(String(args[0]).endsWith('/driver-radio/setup')&&args[1]?.method==='POST')
                    await new Promise(resolve=>window.__releaseRadioSave=resolve);
                return response;
            };""")
            a.locator('#setup-form button').click()
            first.wait_for_function("typeof window.__releaseRadioSave==='function'")
            a.locator('#race-length').fill('38')
            first.evaluate('window.__releaseRadioSave();window.fetch=window.__realRadioFetch')
            first.wait_for_timeout(1100)
            assert a.locator('#race-length').input_value()=='38','save acknowledgement discarded newer edits'
            a.locator('#setup-form button').click();first.wait_for_timeout(1100)
            assert f.f.session.read(f.f.service.refresh()['binding'])['setup']['laps']==38
            a.locator('#race-length').fill('39')
            first.route('**/driver-radio/snapshot',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":{"message":"Synthetic temporary outage"}}'))
            first.wait_for_timeout(1200)
            assert a.locator('#race-length').input_value()=='39','temporary outage discarded the setup draft'
            first.unroute('**/driver-radio/snapshot');first.wait_for_timeout(4200)
            assert a.locator('#race-length').input_value()=='39'
            a.locator('#setup-form button').click();first.wait_for_timeout(1100)
            binding=f.f.service.refresh()['binding']
            old=f.f.service.question('Fuel range?',binding)
            assert f.f.service.attach(old['id'],old['epoch'],{'explanation':'Current range may vary.','ai_state':'ready'})
            f.f.service.question('What about pace?',binding)
            first.wait_for_timeout(1100)
            assert 'Interpretation: Current range may vary.' in a.locator('#history').inner_text(),'history lost a completed interpretation'
            # An edited form keeps its original revision while another view saves.
            a.locator('#race-mode').select_option('laps');a.locator('#race-length').fill('20')
            b.locator('#race-mode').select_option('laps');b.locator('#race-length').fill('24')
            b.locator('#setup-form button').click()
            first.wait_for_timeout(1100)
            a.locator('#setup-form button').click()
            a.locator('#reload').wait_for(state='visible')
            assert 'changed' in a.locator('#problem').inner_text()
            a.locator('#reload').click()
            first.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('race-length').value==='24'")
            b.locator('#service-compound').select_option('RM');b.locator('#service-form button').click()
            first.wait_for_timeout(1200)
            assert f.f.session.read(f.f.service.refresh()['binding'])['service']['compound']=='RM'
            for preset in ('pace','tyres','status'):
                b.locator('[data-preset='+preset+']').click();first.wait_for_timeout(150)
            b.locator('#pause').click();first.wait_for_timeout(1100)
            assert f.native.status()['paused']
            a.locator('#pause').click();first.wait_for_timeout(1100)
            assert not f.native.status()['paused']
            # Provider text is rendered as text, including a hostile-looking transcript.
            f.f.at+=3
            f.f.service.question('<img src=x onerror=window.__injected=true> fuel?',f.f.service.refresh()['binding'])
            first.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('question').textContent.includes('<img')")
            assert first.evaluate('window.__injected===undefined')
            # Keep hostile transcript checks out of operator wiki screenshots.
            f.f.snap['context_revision']+=1;f.f.snap['revision']+=1;f.f.poll()
            binding=f.f.service.refresh()['binding'];declared=f.f.session.read(binding)
            f.f.session.update(binding,dict(binding=binding,expected=declared['revision'],setup={'laps':24,'starting_compound':'RM','fuel_reserve_l':2}))
            f.f.poll();f.native.step();f.f.service.request('pace',binding);f.f.service.request('tyres',binding)
            f.f.service.question('Will the fuel last at this pace?',binding);f.enricher.step('audio')
            first.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('question').textContent.includes('Will the fuel')")
            a.locator('summary').click()
            for width,view,wiki in ((1440,'desktop','console-driver-radio-desktop.png'),(1024,'tablet','console-driver-radio-tablet.png'),(390,'phone','console-driver-radio-phone.png')):
                first.set_viewport_size({'width':width,'height':1000})
                assert first.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'driver desk overflows viewport'
                # Shadow controls must also fit inside the viewport.
                assert first.evaluate("Array.from(document.getElementById('driver-radio').shadowRoot.querySelectorAll('button,input,select,textarea')).filter(n=>n.getBoundingClientRect().width).every(n=>n.getBoundingClientRect().right<=innerWidth+1)")
                shot(first,'driver-radio',view,wiki)
            first.set_viewport_size({'width':1440,'height':1000})
            menu=context.new_page();menu.goto(f.url+'/console');menu.get_by_text('Driver radio',exact=True).wait_for()
            shot(menu,'driver-radio-console',wiki='console-driver-radio-launcher.png')
            # A Director receives radio only through a separate explicit assignment.
            config=f.f.access.read();f.f.access.update({'expected':config['revision'],'drivers':['driver','director']})
            f.f.request('fuel');f.enricher.step('audio')
            panel_context=browser.new_context(viewport={'width':1440,'height':1000})
            panel_context.route('https://fonts.googleapis.com/**',lambda route:route.fulfill(status=200,content_type='text/css',body='/* Offline fixture. */'))
            panel=panel_context.new_page();token=console.m.console_auth.mint_token(console.SECRET,'director')
            panel.goto(f.url+'/console/panel?t='+token)
            panel.locator('#driver-radio-card').wait_for(state='visible')
            panel.locator('#radio-component').locator('[data-preset=fuel]').wait_for(state='visible')
            assert panel.locator('#driver-radio-card').bounding_box()['y']<900,'Solo radio card must be in the initial workspace'
            shot(panel,'driver-radio-panel',wiki='director-panel-solo.png')
            landing=panel_context.new_page();landing.goto(f.url+'/console')
            landing.get_by_text('Director Panel',exact=True).wait_for()
            shot(landing,'driver-radio-director-console',wiki='console-landing.png')
            cc=context.new_page();cc.goto(f.cc_url)
            cc.wait_for_function("document.body.classList.contains('tpl-pov')")
            cc.evaluate("showView('telemetry')")
            cc.locator('#driver-radio-admin').locator('#enabled').wait_for(state='visible')
            cc.wait_for_function("document.getElementById('ver-note').textContent==='dev build'")
            assert cc.locator('#driver-radio-admin').locator('#voice-enabled').is_checked()
            cc.locator('#driver-radio-admin').scroll_into_view_if_needed()
            shot(cc,'driver-radio-control-center',wiki='cc-telemetry.png')
            f.f.service.question('Fuel range?',f.f.service.refresh()['binding'])
            first.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('question').textContent.includes('Fuel range?')")
            # Hold an already-authorized poll while a later request observes revocation.
            first.evaluate("""window.__realRadioFetch=window.fetch;window.__radioReplies=0;
                window.fetch=async(...args)=>{
                    const response=await window.__realRadioFetch(...args);
                    if(String(args[0]).startsWith('/console/driver-radio/')&&args[1]?.method==='GET'){
                        window.__radioReplies++;
                        if(String(args[0]).endsWith('/snapshot'))await new Promise(resolve=>window.__releaseRadioPoll=resolve);
                    }
                    return response;
                };""")
            first.wait_for_function("window.__radioReplies===4&&typeof window.__releaseRadioPoll==='function'")
            config=f.f.access.read();f.f.access.update({'expected':config['revision'],'drivers':['director']})
            a.locator('[data-preset=fuel]').click()
            first.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('content').hidden")
            first.evaluate('window.__releaseRadioPoll();window.fetch=window.__realRadioFetch')
            first.wait_for_timeout(200)
            assert first.evaluate("document.getElementById('driver-radio').shadowRoot.getElementById('content').hidden"),'late authorized poll revived a revoked view'
            assert first.evaluate("document.getElementById('driver-radio').shadowRoot.getElementById('question').textContent===''"),'revoked view retained transcript'
            shot(first,'driver-radio-denied',wiki='console-driver-radio-access.png')
            # Finish only synthetic queued work, then closing views must not pause native service.
            for _ in range(12):
                f.enricher.step('audio');f.native.step();f.f.at+=1
            assert f.native._worker_proc is not None
            owner=f.native._worker_proc
            first.close();second.close();menu.close();cc.close();panel_context.close();f.native.step()
            assert f.native._worker_proc is owner and not owner.closed,'closing views stopped the enabled native service'
            assert not errors,errors
        # Refresh the standard Director image as well, using the production endurance layout.
        with RadioFixture(solo=False) as f:
            page=context.new_page();token=console.m.console_auth.mint_token(console.SECRET,'director')
            page.goto(f.url+'/console/panel?t='+token);page.locator('#pgmBus button').first.wait_for()
            shot(page,'driver-radio-endurance-panel',wiki='director-panel.png');page.close()
        print('PASS driver radio browser: presets, shared work, revision conflicts, native controls, XSS, revocation and closed-view lifetime')
    except Exception:
        results.append(V.SurfaceResult('driver-radio-acceptance','desktop',None,[],0,traceback.format_exc()))
    finally:context.close()
    return results


def main():
    import argparse
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser();parser.add_argument('--report',default=str(ROOT/'runtime/radio-browser-report'));parser.add_argument('--shots')
    args=parser.parse_args()
    import e2e_visual as V
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:results=check(browser,args.report,args.shots)
        finally:browser.close()
    (Path(args.report)/'report.html').write_text(V.render_report(results,[]),encoding='utf8')
    print(V.summarize(results,[]));return V.result_code(results)


if __name__=='__main__':raise SystemExit(main())

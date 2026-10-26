#!/usr/bin/env python3
"""Synthetic production coaching controls in a real browser, without devices or providers."""
import argparse
import sys
import threading
from unittest.mock import patch
import traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def check(browser,outdir,shots=None):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_driver_radio_ui import RadioFixture,console
    import driver_coach_controls as controls
    import e2e_visual as V
    out=Path(outdir);out.mkdir(parents=True,exist_ok=True);results=[]
    def shot(page,name,viewport,wiki):
        path=out/(name+'-'+viewport+'.png');page.screenshot(path=str(path),full_page=True)
        results.append(V.SurfaceResult(name,viewport,path.name,[],0,None))
        if shots:
            import shutil
            target=Path(shots)/wiki;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
            slide=ROOT/'src/docs/slides/assets/img'/wiki
            if slide.exists():shutil.copyfile(path,slide)
    def sync(page,f,name='radioView',subject='console:driver'):
        revision=f.practice.service.snapshot({'profile':'solo-pov','subject':subject})['revision']
        page.wait_for_function("([name,revision])=>window[name]?.coach.state?.revision===revision&&!window[name].coach.state.pending",arg=[name,revision])
    def apply(page,host,button,f,name='radioView',subject='console:driver'):
        with page.expect_response(lambda response:response.url.endswith('/coach') and response.request.method=='POST') as response:
            host.locator(button).click()
        data=response.value.json();assert response.value.status==200,data
        f.practice.service.step();sync(page,f,name,subject)
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.route('https://fonts.googleapis.com/**',lambda route:route.fulfill(status=200,content_type='text/css',body='/* Offline synthetic fixture. */'))
    try:
        with RadioFixture(coaching=True) as f:
            f.practice.wall=1767225600.0
            f.practice.snap['receipt']['received_at']=f.practice.wall
            f.practice.snap['current']['_start_wall']=f.practice.wall
            page=context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f.url+'/console/driver-radio?t='+f.token)
            a=page.locator('#driver-radio')
            a.locator('#coach-start').wait_for(state='visible',timeout=5000)
            assert not f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})['active'],'Opening the desk started training'
            a.locator('#coach-objective').select_option('consistency')
            a.locator('#coach-start').click()
            page.wait_for_function("window.radioView.coach.state?.pending")
            f.practice.service.step()
            for n,seconds in ((2,88.8),(3,88.4),(4,88.5)):
                f.practice.complete(n,seconds);f.practice.service.step()
            page.wait_for_function("window.radioView.coach.state?.focus?.focus")
            assert a.locator('#coach-focus').inner_text()
            assert a.locator('#coach-review').inner_text()
            sync(page,f);apply(page,a,'#coach-keep',f)
            a.locator('#coach-show-evidence').click()
            a.locator('#coach-history').evaluate('(node)=>node.open=true')
            a.locator('#coach-history-entries details').first.evaluate('(node)=>node.open=true')
            assert a.locator('#coach-evidence').inner_text().find('Observation')>=0
            assert all(ident in a.locator('#coach-evidence').inner_text() for ident in f.practice.service.view({'profile':'solo-pov','subject':'console:driver'})['focus']['baseline']['lap_ids'])
            # Start a reference read, then let a new lap change the actor revision.
            # Stop must remain actionable while neither the old read nor old GET replies finish.
            resolver=controls.References.resolve
            entered=threading.Event();release=threading.Event()
            def delayed(self,ident):
                value=resolver(self,ident);entered.set();assert release.wait(6);return value
            page.evaluate("""window.__coachFetch=window.fetch;window.__heldCoachGets=[];
                window.fetch=async(...args)=>{const response=await window.__coachFetch(...args);
                    if(String(args[0]).endsWith('/coach')&&args[1]?.method!=='POST')
                        await new Promise(resolve=>window.__heldCoachGets.push(resolve));
                    return response;};""")
            try:
                with patch.object(controls.References,'resolve',delayed):
                    option=a.locator('#coach-reference-choice option').nth(1).get_attribute('value')
                    a.locator('#coach-reference-choice').select_option(option);a.locator('#coach-use-reference').click()
                    assert entered.wait(2),'Reference lookup did not begin'
                    f.practice.complete(5,88.3);f.practice.service.step()
                    assert a.locator('#coach-stop').is_enabled(),'A pending reference disabled Stop'
                    a.locator('#coach-stop').click()
                    page.wait_for_function("!window.radioView.coach.sending")
                    assert not f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})['active'],'Stale display revision prevented Stop during reference I/O'
            finally:
                release.set();page.evaluate('window.__heldCoachGets.forEach(resolve=>resolve());window.fetch=window.__coachFetch')
            f.practice.service.step()
            sync(page,f)
            # Draft edits made while a control response is delayed remain visible.
            a.locator('#coach-objective').select_option('consistency');apply(page,a,'#coach-start',f)
            for n,seconds in ((6,88.6),(7,88.7),(8,88.3),(9,88.4)):
                f.practice.complete(n,seconds);f.practice.service.step()
            sync(page,f);page.wait_for_function("window.radioView.coach.state?.focus?.focus")
            page.evaluate("""window.__draftFetch=window.fetch;window.fetch=async(...args)=>{
                const response=await window.__draftFetch(...args);
                if(String(args[0]).endsWith('/coach')&&args[1]?.method==='POST'&&JSON.parse(args[1].body).action==='objective')
                    await new Promise(resolve=>window.__releaseCoachDraft=resolve);
                return response;};""")
            a.locator('#coach-objective').select_option('pace');a.locator('#coach-goal').click()
            page.wait_for_function("typeof window.__releaseCoachDraft==='function'")
            a.locator('#coach-objective').select_option('balanced')
            page.evaluate('window.__releaseCoachDraft();window.fetch=window.__draftFetch')
            page.wait_for_function("window.radioView.coach.state?.pending");f.practice.service.step();sync(page,f)
            assert a.locator('#coach-objective').input_value()=='balanced','Control acknowledgement discarded newer edits'
            a.locator('#coach-goal').click();a.locator('#coach-reload').wait_for(state='visible')
            page.wait_for_timeout(1200)
            assert 'changed' in a.locator('#coach-error').inner_text(),'A background poll cleared the actionable conflict'
            assert f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})['calibration']['objective']=='pace'
            a.locator('#coach-reload').click()
            page.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('coach-objective').value==='pace'")
            a.locator('#coach-skill').select_option('consistency');apply(page,a,'#coach-set-skill',f)
            for n,seconds in ((10,88.5),(11,88.2),(12,88.3),(13,88.05),(14,88.15),(15,88.05)):
                f.practice.complete(n,seconds);f.practice.service.step()
                if n==12:
                    sync(page,f);apply(page,a,'#coach-keep',f)
            sync(page,f);page.wait_for_function("window.radioView.coach.state?.focus?.focus")
            # Current facts render as text even when a definition label looks hostile.
            def hostile(route):
                response=route.fetch();data=response.json()
                if (data.get('focus') or {}).get('focus'):data['focus']['focus']['label']='<img src=x onerror=window.__coachInjected=true>'
                route.fulfill(response=response,json=data)
            page.route('**/driver-radio/coach',hostile)
            page.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('coach-label').textContent.includes('<img')")
            assert page.evaluate('window.__coachInjected===undefined')
            page.unroute('**/driver-radio/coach');page.wait_for_function("!document.getElementById('driver-radio').shadowRoot.getElementById('coach-label').textContent.includes('<img')")
            a.locator('#coach-show-evidence').click()
            a.locator('#coach-history').evaluate('(node)=>node.open=true')
            a.locator('#coach-history-entries details').first.evaluate('(node)=>node.open=true')
            for width,view in ((1440,'desktop'),(1024,'tablet'),(390,'phone')):
                page.set_viewport_size({'width':width,'height':1000})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Driver coaching overflows viewport'
                assert page.evaluate("Array.from(document.getElementById('driver-radio').shadowRoot.querySelectorAll('button,input,select')).filter(n=>n.getBoundingClientRect().width).every(n=>n.getBoundingClientRect().right<=innerWidth+1)")
                shot(page,'coach-driver',view,'console-driver-radio-'+view+'.png')
            page.set_viewport_size({'width':1440,'height':1000})
            # Local producer training is a different owner from the Console driver.
            apply(page,a,'#coach-stop',f)
            cc=context.new_page();cc.goto(f.cc_url);cc.evaluate("showView('telemetry')")
            c=cc.locator('#driver-radio-admin');c.locator('#coach-start').wait_for(state='visible')
            cc.wait_for_function("document.getElementById('ver-note').textContent==='dev build'")
            sync(cc,f,'radioAdmin','local:producer');c.locator('#coach-mode').select_option('time_trial');c.locator('#coach-objective').select_option('consistency');apply(cc,c,'#coach-start',f,'radioAdmin','local:producer')
            for n,seconds in ((16,88.4),(17,88.3),(18,88.2),(19,88.3)):
                f.practice.complete(n,seconds);f.practice.service.step()
            sync(cc,f,'radioAdmin','local:producer');cc.wait_for_function("window.radioAdmin.coach.state?.focus?.focus")
            assert f.practice.service.snapshot({'profile':'solo-pov','subject':'local:producer'})['calibration']['mode']=='time_trial'
            c.locator('#coach-history').evaluate('(node)=>node.open=true')
            c.locator('#coach-history-entries details').first.evaluate('(node)=>node.open=true')
            c.locator('#coach-history').scroll_into_view_if_needed()
            shot(cc,'coach-control-center','desktop','cc-telemetry.png')
            apply(cc,c,'#coach-stop',f,'radioAdmin','local:producer')
            config=f.f.access.read();f.f.access.update(dict(expected=config['revision'],drivers=['driver','director']))
            panel_context=browser.new_context(viewport={'width':1440,'height':1000})
            panel_context.route('https://fonts.googleapis.com/**',lambda route:route.fulfill(status=200,content_type='text/css',body='/* Offline fixture. */'))
            try:
                panel=panel_context.new_page();token=console.m.console_auth.mint_token(console.SECRET,'director')
                panel.goto(f.url+'/console/panel?t='+token)
                p=panel.locator('#radio-component');p.locator('#coach-start').wait_for(state='visible')
                assert p.locator('#coach-skill').is_hidden(),'Compact panel exposed the full review form'
                sync(panel,f,'radioView','console:director');apply(panel,p,'#coach-start',f,'radioView','console:director')
                for n,seconds in ((20,88.5),(21,88.4),(22,88.3),(23,88.4)):
                    f.practice.complete(n,seconds);f.practice.service.step()
                sync(panel,f,'radioView','console:director');panel.wait_for_function("window.radioView.coach.state?.focus?.focus")
                assert panel.locator('#driver-radio-card').bounding_box()['y']<900
                shot(panel,'coach-panel','desktop','director-panel-solo.png')
                # Views are optional; closing one cannot stop relay-owned training.
                panel.close()
                assert f.practice.service.snapshot({'profile':'solo-pov','subject':'console:director'})['active']
            finally:panel_context.close()
            assert not errors,errors
            page.close();cc.close()
            results.append(V.SurfaceResult('driver-coach-controls','desktop',None,[],0,None))
        optional_ai_visibility(browser)
        results.append(V.SurfaceResult('driver-coach-optional-ai','desktop',None,[],0,None))
        ordering_and_identity(browser)
        results.append(V.SurfaceResult('driver-coach-privacy-ordering','desktop',None,[],0,None))
        history_lifecycle(browser)
        history_lifecycle(browser, reverse=True)
        history_source_ordering(browser)
        results.append(V.SurfaceResult('driver-coach-private-history','desktop',None,[],0,None))
    except Exception:
        results.append(V.SurfaceResult('driver-coach','desktop',None,[],0,traceback.format_exc()))
    finally:context.close()
    return results


def optional_ai_visibility(browser):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_driver_radio_ui import RadioFixture
    import driver_radio_providers as providers
    class AI:
        def explain(self,*args):
            return dict(explanation=providers.COACH_EXPLANATIONS[1],evidence=['coach.lap_time_s'],claims=[],exercise='repeat')
    context=browser.new_context()
    try:
        with RadioFixture(coaching=True) as f:
            config=f.f.access.read();f.f.access.update(dict(expected=config['revision'],providers=dict(speech=False,agent='fixture')))
            page=context.new_page();page.goto(f.url+'/console/driver-radio?t='+f.token)
            host=page.locator('#driver-radio');host.locator('#coach-ai').check();host.locator('#coach-start').click()
            page.wait_for_function('window.radioView.coach.state?.pending');f.practice.service.step()
            for n in (2,3):f.practice.complete(n);f.practice.service.step()
            f.enricher.ai=AI();assert f.enricher.step('ai')
            page.wait_for_function("document.getElementById('driver-radio').shadowRoot.getElementById('coach-review').textContent.includes('AI interpretation:')")
            assert providers.COACH_EXPLANATIONS[1] in host.locator('#coach-review').inner_text()
            f.practice.complete(4);f.practice.service.step()
            page.wait_for_function("window.radioView.coach.state?.latest?.lap_id==='lap-4'")
            assert 'AI interpretation:' not in host.locator('#coach-review').inner_text(),'Previous-lap AI survived the current review'
            page.close()
    finally:context.close()


def ordering_and_identity(browser):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_driver_radio_ui import RadioFixture,console
    import driver_coach_service as service
    context=browser.new_context()
    try:
        with RadioFixture(coaching=True) as f:
            page=context.new_page();page.goto(f.url+'/console/driver-radio?t='+f.token)
            a=page.locator('#driver-radio');a.locator('#coach-start').click()
            page.wait_for_function('window.radioView.coach.state?.pending');f.practice.service.step()
            for n in (2,3):f.practice.complete(n);f.practice.service.step()
            revision=f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})['revision']
            page.wait_for_function('(rev)=>window.radioView.coach.state?.revision===rev',arg=revision)
            page.evaluate("""window.__orderFetch=fetch;window.fetch=async(...args)=>{
                const response=await window.__orderFetch(...args);
                if(String(args[0]).endsWith('/coach')&&args[1]?.method!=='POST'&&!window.__releaseOldCoach)
                    await new Promise(resolve=>window.__releaseOldCoach=resolve);
                return response;};""")
            page.wait_for_function('typeof window.__releaseOldCoach==="function"')
            a.locator('#coach-objective').select_option('pace');a.locator('#coach-goal').click()
            page.wait_for_function('window.radioView.coach.state?.pending')
            accepted=page.evaluate('window.radioView.coach.state.revision')
            page.evaluate('window.__releaseOldCoach();window.fetch=window.__orderFetch')
            page.wait_for_function('!window.radioView.coach.polling')
            assert page.evaluate('window.radioView.coach.state.revision')>=accepted,'Late GET replaced a newer command acknowledgement'
            f.practice.service.step()
            # A new coordinator has a lower counter but a different lifetime.
            old=f.practice.service;old_generation=old.snapshot({'profile':'solo-pov','subject':'console:driver'})['generation']
            old.close()
            fresh=service.Service('solo-pov',old.source,old.detail,f.f.service,clock=lambda:f.practice.at,wall=lambda:f.practice.wall)
            f.practice.service=fresh;f.f.service.coach=fresh
            page.wait_for_function('(old)=>window.radioView.coach.state?.generation!==old&&window.radioView.coach.state?.revision===0',arg=old_generation)
            assert not page.evaluate('window.radioView.coach.state.active')
            assert not a.locator('#coach-review').inner_text(),'Old relay review survived restart'
            page.close()
    finally:context.close()
    for change in ('identity','revocation'):
        context=browser.new_context()
        try:
            with RadioFixture(coaching=True) as f:
                config=f.f.access.read();f.f.access.update(dict(expected=config['revision'],drivers=['driver','other']))
                page=context.new_page();page.goto(f.url+'/console/driver-radio?t='+f.token)
                a=page.locator('#driver-radio');a.locator('#coach-start').click()
                page.wait_for_function('window.radioView.coach.state?.pending');f.practice.service.step()
                for n in (2,3):f.practice.complete(n);f.practice.service.step()
                page.wait_for_function('window.radioView.coach.state?.focus?.focus')
                page.evaluate("""window.__scopeFetch=fetch;window.fetch=async(...args)=>{
                    const response=await window.__scopeFetch(...args);
                    if(String(args[0]).endsWith('/coach')&&args[1]?.method!=='POST'&&!window.__releasePrivateCoach)
                        await new Promise(resolve=>window.__releasePrivateCoach=resolve);
                    return response;};""")
                page.wait_for_function('typeof window.__releasePrivateCoach==="function"')
                page.evaluate("""const coach=window.radioView.coach,adopt=coach.adopt;
                    coach.adopt=function(state,...args){const accepted=adopt.call(this,state,...args);
                        if(accepted&&state.viewer==='console:driver'&&state.focus?.focus)window.__privateCoachRevived=true;
                        return accepted;}; void 0;""")
                if change=='identity':
                    token=console.m.console_auth.mint_token(console.SECRET,'other')
                    context.add_cookies([dict(name='rc_console',value=token,domain='127.0.0.1',path='/console',httpOnly=True,sameSite='Lax')])
                else:
                    config=f.f.access.read();f.f.access.update(dict(expected=config['revision'],drivers=[]));f.practice.service.step()
                page.evaluate('window.__releasePrivateCoach();window.fetch=window.__scopeFetch')
                page.wait_for_function('!window.radioView.coach.polling')
                assert not page.evaluate('window.__privateCoachRevived'),'A delayed private reply rendered before the next current-identity poll'
                assert not a.locator('#coach-evidence').inner_text(),'A delayed private reply rendered before the next current-identity poll'
                if change=='identity':page.wait_for_function("window.radioView.coach.state?.viewer==='console:other'")
                else:page.wait_for_function('window.radioView.coach.state===null')
                assert not a.locator('#coach-review').inner_text(),'Private review survived identity or permission change'
                assert not a.locator('#coach-evidence').inner_text(),'Private evidence remained in the DOM'
                assert not a.locator('#coach-history-entries').inner_text(),'Private history remained in the DOM'
                assert not a.locator('#coach-debrief').inner_text(),'Private debrief survived identity or permission change'
                assert not page.evaluate('window.radioView.coach.state?.focus?.focus'),'Late old reply restored private focus'
                page.close()
        finally:context.close()


def history_lifecycle(browser, reverse=False):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_driver_radio_ui import RadioFixture
    context=browser.new_context()
    try:
        with RadioFixture(coaching=True) as f:
            current=f.practice.current
            f.practice.current=lambda n:dict(current(n),settings_confirmed=True)
            f.practice.snap['current']=f.practice.current(1)
            page=context.new_page();page.goto(f.url+'/console/driver-radio?t='+f.token)
            host=page.locator('#driver-radio')
            def settle():
                owner={'profile':'solo-pov','subject':'console:driver'}
                revision=f.practice.service.snapshot(owner)['revision']
                history_revision=f.practice.service.journal.read(owner,f.practice.service._generation)['revision']
                page.wait_for_function('([actor,history])=>window.radioView.coach.state?.revision===actor&&window.radioView.coach.state?.history?.revision===history&&!window.radioView.coach.state.pending&&!window.radioView.coach.sending',arg=[revision,history_revision])
            def command(selector):
                with page.expect_response(lambda response:response.url.endswith('/coach') and response.request.method=='POST') as response:
                    host.locator(selector).click()
                data=response.value.json()
                assert response.value.status==200,data
                page.wait_for_function('window.radioView.coach.state?.pending')
                f.practice.service.step();settle()
            host.locator('#coach-history').wait_for(state='visible',timeout=5000)
            command('#coach-start')
            for n in (2,3,4):f.practice.complete(n,settings_confirmed=True);f.practice.service.step()
            settle()
            page.wait_for_function("window.radioView.coach.state?.history?.entries.some(r=>r.latest?.lap_id==='lap-4')")
            host.locator('#coach-history').evaluate('(node)=>node.open=true')
            host.locator('#coach-history-entries details').first.evaluate('(node)=>node.open=true')
            assert 'Completed lap' in host.locator('#coach-history-entries').inner_text()
            command('#coach-stop')
            page.wait_for_function("window.radioView.coach.state?.history?.entries.some(r=>r.status==='stopped')")
            assert 'fresh' in host.locator('#coach-debrief').inner_text().lower()
            ended=page.evaluate("window.radioView.coach.state.history.entries.find(r=>r.status==='stopped').id")
            command('#coach-start')
            assert not f.practice.service.view({'profile':'solo-pov','subject':'console:driver'})['calibration']['calibration_ids']
            for n in (6,7,8):f.practice.complete(n,settings_confirmed=True);f.practice.service.step()
            settle()
            page.wait_for_function('window.radioView.coach.state?.history_suggestion')
            hint=page.evaluate('window.radioView.coach.state.history_suggestion.skill')
            command('#coach-apply-history')
            page.wait_for_function('(skill)=>window.radioView.coach.state?.focus?.skill===skill&&!window.radioView.coach.state.pending',arg=hint)
            assert set(f.practice.service.view({'profile':'solo-pov','subject':'console:driver'})['calibration']['baseline']['lap_ids']).isdisjoint({'lap-2','lap-3'})
            history_revision=f.practice.service.journal.read({'profile':'solo-pov','subject':'console:driver'},f.practice.service._generation)['revision']
            page.wait_for_function('(revision)=>window.radioView.coach.state?.history?.revision===revision',arg=history_revision)
            # Drive this ordering explicitly; a scheduled poll must not become the
            # held GET before the DELETE has entered its journal operation.
            page.evaluate('clearTimeout(window.radioView.coach.timer);window.radioView.coach.schedule=()=>{};void 0')
            page.wait_for_function('!window.radioView.coach.polling')
            host.locator('#coach-objective').select_option('pace')
            host.locator('[data-history-id="'+ended+'"]').evaluate('(node)=>node.open=true')
            store=f.practice.service.journal;original=store.delete;entered=threading.Event();release=threading.Event()
            def delayed(*args):
                entered.set();assert release.wait(6);return original(*args)
            page.evaluate("""window.__historyFetch=fetch;window.fetch=async(...args)=>{
                const response=await window.__historyFetch(...args);
                if(String(args[0]).endsWith('/coach')&&args[1]?.method==='POST'&&window.__reverseHistory)
                    await new Promise(resolve=>window.__releaseDeleteReply=resolve);
                if(String(args[0]).endsWith('/coach')&&args[1]?.method!=='POST'&&!window.__releaseHistoryReply)
                    await new Promise(resolve=>window.__releaseHistoryReply=resolve);
                return response;};""")
            page.evaluate('(reverse)=>window.__reverseHistory=reverse',reverse)
            try:
                with patch.object(store,'delete',delayed):
                    with page.expect_response(lambda response:response.url.endswith('/coach') and response.request.method=='POST') as response:
                        host.locator('[data-history-id="'+ended+'"] button').click()
                        assert entered.wait(2)
                        page.evaluate('clearTimeout(window.radioView.coach.timer);void window.radioView.coach.poll()')
                        page.wait_for_function('typeof window.__releaseHistoryReply==="function"')
                        release.set()
                    assert response.value.status==200,response.value.json()
                    if reverse:
                        page.wait_for_function('typeof window.__releaseDeleteReply==="function"')
                        page.evaluate('window.__releaseHistoryReply()')
                        page.wait_for_function('!window.radioView.coach.polling')
                        page.evaluate('window.__releaseDeleteReply()')
                        page.wait_for_function('!window.radioView.coach.sending')
                        assert not page.evaluate('(id)=>window.radioView.coach.state.history.entries.some(r=>r.id===id)',ended),'A newer old-history GET suppressed the successful deletion acknowledgement'
                    page.wait_for_function('(id)=>!window.radioView.coach.sending&&!window.radioView.coach.state.history.entries.some(r=>r.id===id)',arg=ended)
                    deleted_revision=page.evaluate('window.radioView.coach.state.history.revision')
                    page.evaluate('window.__releaseHistoryReply();window.fetch=window.__historyFetch')
                    page.wait_for_function('!window.radioView.coach.polling')
                    assert not host.locator('[data-history-id="'+ended+'"]').count(),'An older history reply restored a deleted summary'
                    assert page.evaluate('window.radioView.coach.state.history.revision')>=deleted_revision
                    assert host.locator('#coach-objective').input_value()=='pace','History deletion discarded an unsaved objective'
            finally:
                release.set();page.evaluate('window.__releaseHistoryReply?.();window.__releaseDeleteReply?.();window.fetch=window.__historyFetch')
            host.locator('#coach-history-delete-all').click()
            page.wait_for_function('window.radioView.coach.state?.history?.entries.length===0')
            assert not host.locator('#coach-debrief').inner_text()
            f.practice.complete(9,settings_confirmed=True);f.practice.service.step()
            assert not store.read({'profile':'solo-pov','subject':'console:driver'},f.practice.service._generation)['entries']
            page.close()
            assert f.practice.service.snapshot({'profile':'solo-pov','subject':'console:driver'})['active']
    finally:context.close()


def history_source_ordering(browser):
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_driver_radio_ui import RadioFixture
    context = browser.new_context()
    try:
        with RadioFixture(coaching=True) as f:
            page = context.new_page()
            page.goto(f.url + '/console/driver-radio?t=' + f.token)
            host = page.locator('#driver-radio')
            host.locator('#coach-start').click()
            page.wait_for_function('window.radioView.coach.state?.pending')
            f.practice.service.step()
            for n in (2, 3, 4):
                f.practice.complete(n)
                f.practice.service.step()
            page.wait_for_function('window.radioView.coach.state?.focus?.focus&&window.radioView.coach.state.history?.entries.length')
            host.locator('#coach-history').evaluate('(node)=>node.open=true')
            previous = page.evaluate('window.radioView.coach.state.revision')
            page.evaluate("window.__sourceFetch=fetch;window.fetch=async(...args)=>{\n    const response=await window.__sourceFetch(...args);\n    if(String(args[0]).endsWith('/coach')&&args[1]?.method!=='POST'&&!window.__releaseSourceReply&&(await response.clone().json()).focus?.focus)\n      await new Promise(resolve=>window.__releaseSourceReply=resolve);\n    return response;};")
            page.wait_for_function('typeof window.__releaseSourceReply==="function"')
            f.practice.snap['current']['car_id'] = 99
            host.locator('#coach-history-delete-all').click()
            page.wait_for_function('window.radioView.coach.state?.phase==="paused"&&!window.radioView.coach.sending')
            assert page.evaluate('window.radioView.coach.state.revision') == previous, 'Fixture unexpectedly changed actor revision'
            page.evaluate('window.__releaseSourceReply();window.fetch=window.__sourceFetch')
            page.wait_for_function('!window.radioView.coach.polling')
            assert not page.evaluate('window.radioView.coach.state?.focus?.focus'), 'An old equal-revision GET revived live facts after a newer context-aware acknowledgement'
            page.close()
    finally:
        context.close()


def main():
    from playwright.sync_api import sync_playwright
    import e2e_visual as V
    parser=argparse.ArgumentParser();parser.add_argument('--report',default=str(ROOT/'runtime/coach-browser-report'));parser.add_argument('--shots')
    args=parser.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        try:results=check(browser,args.report,args.shots)
        finally:browser.close()
    (Path(args.report)/'report.html').write_text(V.render_report(results,[]),encoding='utf8')
    print(V.summarize(results,[]));return V.result_code(results)


if __name__=='__main__':raise SystemExit(main())

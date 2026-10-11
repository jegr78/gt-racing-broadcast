#!/usr/bin/env python3
"""Real-browser optional analysis checks with synthetic telemetry and a controlled CLI."""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from ai_ui_fixture import Fixture,web_fixture


async def verify(page,fixture,port):
    errors=[];page.on('pageerror',lambda error:errors.append(error.stack))
    await page.goto('http://127.0.0.1:'+str(port))
    await page.wait_for_function('() => window.RacecastAI && tmState.profile === "profile"')
    premature=[];page.on('request',lambda request:premature.append(request.url) if '/api/ai/' in request.url and 'profile=null' in request.url else None)
    await page.evaluate('() => {tmState.profile=null;RacecastAI.reset(null);showView("telemetry");}')
    await asyncio.sleep(.2)
    assert not premature,'profile-scoped AI requests were sent before profile initialization'
    await page.evaluate('() => {tmState.profile="profile";RacecastAI.reset("profile");}')
    await page.evaluate('() => showView("settings")')
    await page.evaluate('() => RacecastAI.settings()')
    await page.locator('#ai-configs').select_option('coach');await page.locator('#ai-check').click()
    await page.wait_for_function('() => document.getElementById("ai-model-suggestions").children.length === 1')
    await page.locator('#ai-configs').select_option('');await page.locator('#ai-name').fill('Second coach')
    await page.locator('#ai-config-model').fill('exact-other');await page.locator('#ai-save').click()
    await page.wait_for_function('() => [...document.getElementById("ai-configs").options].some(o => o.textContent.includes("Second coach"))')
    await page.locator('#ai-enabled').uncheck();await page.locator('#ai-save').click()
    await page.wait_for_function('() => document.getElementById("ai-load-selection").disabled')
    await page.locator('#ai-enabled').check();await page.locator('#ai-save').click()
    await page.wait_for_function('() => !document.getElementById("ai-load-selection").disabled')
    await page.locator('#ai-remove').click()
    await page.wait_for_function('() => document.getElementById("ai-configs").options.length === 2')
    await page.evaluate('() => showView("telemetry")')
    await page.wait_for_function('() => tmState.lapB && tmState.lapB.recording_id')
    before=fixture.control().history()['runs'];assert not before
    await page.locator('#ai-load-selection').click();await page.wait_for_selector('#ai-laps input')
    assert await page.locator('#ai-references input:checked').count()==0
    await page.locator('#ai-language').fill('de')
    async def preview():
        await page.locator('#ai-preview-button').click()
        await page.wait_for_function('() => !document.getElementById("ai-start").disabled')
        return None
    await page.locator('#ai-model').fill('alternate');await preview()
    await page.evaluate('() => RacecastAI.settings()')
    assert await page.locator('#ai-model').input_value()=='alternate'
    assert await page.locator('#ai-start').is_disabled()
    await preview()
    assert 'cloud' in (await page.locator('#ai-preview').inner_text()).lower()
    assert not fixture.control().history()['runs'],'preview invoked a provider'
    await page.locator('#ai-goal').fill('changed');assert await page.locator('#ai-start').is_disabled()
    # A delayed old preview must not restore permission to start changed inputs.
    async def delayed(route):
        response=await route.fetch();await asyncio.sleep(.3);await route.fulfill(response=response)
    await page.route('**/api/ai/preview',delayed)
    await page.locator('#ai-preview-button').click();await page.locator('#ai-goal').fill('changed again')
    await asyncio.sleep(.5);assert await page.locator('#ai-start').is_disabled()
    await page.unroute('**/api/ai/preview',delayed)
    arrived=asyncio.Event();finished=asyncio.Event()
    async def delayed_job(route):
        response=await route.fetch();arrived.set();await asyncio.sleep(.3);await route.fulfill(response=response);finished.set()
        return None
    await page.route('**/api/ai/job?*',delayed_job)
    await preview();await page.locator('#ai-start').click()
    await asyncio.wait_for(arrived.wait(),10)
    await page.locator('#ai-goal').fill('Prepare a future goal while this run finishes')
    await asyncio.wait_for(finished.wait(),5)
    await page.unroute('**/api/ai/job?*',delayed_job)
    try:await page.wait_for_selector('#ai-report article',timeout=6000)
    except Exception as error:raise AssertionError('completed job was lost after editing a future goal') from error
    # A second machine-wide job can finish while an older completion reply is held.
    held=asyncio.Event();release=asyncio.Event();delivered=asyncio.Event();captured=False
    async def older_completion(route):
        nonlocal captured
        response=await route.fetch()
        if not captured:
            captured=True;held.set();await release.wait();await route.fulfill(response=response);delivered.set()
        else:await route.fulfill(response=response)
        return None
    await page.route('**/api/ai/job?*',older_completion)
    await preview();await page.locator('#ai-start').click();await asyncio.wait_for(held.wait(),10)
    fixture.delay=.2;control=fixture.control();external=dict(rec=fixture.source.name,session=1,agent='coach',model='EXTERNAL_NEW_JOB',language='de')
    reviewed=control.preview(external);control.start(dict(external,confirm_preview=reviewed['confirm_preview']))
    await page.evaluate('() => RacecastAI.status()')
    for _ in range(100):
        if not control.status()['busy']:break
        await asyncio.sleep(.02)
    assert not control.status()['busy']
    release.set();await asyncio.wait_for(delivered.wait(),5);await page.unroute('**/api/ai/job?*',older_completion)
    try:await page.wait_for_function('() => document.getElementById("ai-report").textContent.includes("EXTERNAL_NEW_JOB")',timeout=6000)
    except Exception as error:raise AssertionError('older completion discarded the newer completed job') from error
    assert await page.locator('#ai-report article').count()==4
    assert await page.evaluate('() => window.aiInjected') is None
    assert 'Report language: de' in await page.locator('#ai-report').inner_text()
    async with page.expect_download() as pending:
        await page.get_by_role('button',name='Export html',exact=True).click()
    download=await pending.value
    html=Path(await download.path()).read_text()
    assert '&lt;script&gt;' in html and '<script>window.aiInjected' not in html
    await page.locator('#ai-report').get_by_role('button',name='Compare with S1 lap 3').first.click()
    await page.wait_for_function('() => tmState.lapA?.lap === 3 && tmState.lapB?.lap === 2')
    async def replaced_reference(route):
        response=await route.fetch();data=await response.json()
        if data.get('lap',{}).get('lap')==3:data['lap']['recording_id']='0'*32
        await route.fulfill(json=data)
    await page.route('**/api/telemetry/lap?*',replaced_reference)
    await page.evaluate('() => tmLapCache.clear()')
    await page.locator('#ai-report').get_by_role('button',name='Compare with S1 lap 3').first.click()
    for _ in range(40):
        if 'Comparison recording identity changed' in await page.locator('#tm-err').inner_text():break
        await asyncio.sleep(.05)
    assert 'Comparison recording identity changed' in await page.locator('#tm-err').inner_text(),'changed comparison identity accepted'
    await page.unroute('**/api/telemetry/lap?*',replaced_reference)
    await page.evaluate('() => tmLapCache.clear()')
    # Opening a report uses the real ordinary telemetry callbacks and exact identity.
    await page.locator('#ai-report').get_by_role('button',name='Open lap at 500 m').first.click()
    await page.wait_for_function('() => tmState.lapB?.lap === 2 && tmState.lapB.recording_id')
    assert await page.evaluate('() => tmState.lapB.lap')==2
    fixture.invalid=True
    await preview();await page.locator('#ai-start').click()
    await page.wait_for_function('() => document.getElementById("ai-run-status").textContent.startsWith("failed")',timeout=15000)
    assert await page.locator('#ai-report article').count()==0
    fixture.invalid=False;fixture.delay=3
    await preview();await page.locator('#ai-start').click()
    await page.wait_for_function('() => !document.getElementById("ai-cancel").disabled',timeout=10000)
    await page.locator('#ai-cancel').click()
    await page.wait_for_function('() => document.getElementById("ai-run-status").textContent.startsWith("cancelled")',timeout=15000)
    assert await page.locator('#ai-report article').count()==0 and not fixture.control().status()['busy']
    # Another tab can change the server profile before this page has updated.
    from ai_ui_fixture import package_fixture
    fixture.sources['other']=package_fixture.source(fixture.root/'other/telemetry-recordings')
    fixture.profile='other';fixture.delay=.5
    control=fixture.control();payload=dict(rec=fixture.sources['other'].name,session=1,agent='coach',model='OTHER_PROFILE_PRIVATE_MODEL')
    prepared=control.preview(payload);run=control.start(dict(payload,confirm_preview=prepared['confirm_preview']),background=False)
    assert run['state']=='completed'
    async def foreign_history(route):
        await route.fulfill(json=control.history())
    await page.route('**/api/ai/history?*',foreign_history)
    await page.evaluate('() => RacecastAI.history()')
    assert 'OTHER_PROFILE_PRIVATE_MODEL' not in await page.locator('#ai-history').inner_text()
    await page.unroute('**/api/ai/history?*',foreign_history)
    await page.evaluate('() => RacecastAI.history()')
    assert 'OTHER_PROFILE_PRIVATE_MODEL'  not in await page.locator('#ai-history').inner_text()
    assert await page.evaluate('() => tmState.profile')=='profile'
    await page.evaluate('() => useProfile("other")')
    await page.wait_for_function('() => tmState.profile === "other"')
    assert await page.locator('#ai-report').inner_text()==''
    assert 'Synthetic coach' not in await page.locator('#ai-history').inner_text()
    assert fixture.control().history()['runs']
    assert not errors,errors
    print('PASS browser: explicit preview/start, changed/delayed inputs, text-only report/export/lap, validation failure, cancellation, profile isolation')
    return None  # The repository distinguishes coroutine results from synchronous procedures.


async def main():
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix='racecast-ai-browser-') as root:
        fixture=Fixture(root);server,port=web_fixture._serve(fixture.context())
        try:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                page=await browser.new_page(viewport={'width':1280,'height':900})
                await verify(page,fixture,port)
                await browser.close()
        finally:server.shutdown();server.server_close()
    return None


if __name__=='__main__':asyncio.run(main())

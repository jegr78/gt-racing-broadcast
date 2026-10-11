#!/usr/bin/env python3
"""Real producer/Console browser lifecycle with synthetic data and no accounts."""
import asyncio
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from engineer_ui_fixture import Fixture,web_fixture,gate


async def verify(pw,fixture,ui,console,shots=False):
    browser=await pw.chromium.launch(headless=True)
    page=await browser.new_page(viewport={'width':1440,'height':900})
    errors=[];page.on('pageerror',lambda error:errors.append(error.stack))
    await page.goto('http://127.0.0.1:'+str(ui))
    await page.wait_for_function('() => tmState.profile==="profile" && window.RacecastEngineer')
    await page.evaluate('() => showView("telemetry")')
    await page.wait_for_function('() => !document.getElementById("engineer-save").disabled')
    assert not await page.locator('#engineer-enabled').is_checked()
    await page.locator('#engineer-enabled').check();await page.locator('#engineer-save').click()
    await page.wait_for_function('() => document.getElementById("engineer-status").textContent.startsWith("Engineer access is enabled")')
    assert fixture.access().read()['enabled']
    await page.locator('#engineer-label').fill('<img src=x onerror="window.engineerInjected=true">')
    await page.locator('#engineer-grant').click();await page.wait_for_selector('#engineer-grants li')
    assert not await page.evaluate('() => !!window.engineerInjected')
    await page.evaluate('() => showView("profile")');await page.wait_for_selector('[data-crew-row="1"]')
    row=page.locator('[data-crew-row="1"]')
    await row.get_by_label('Race Engineer',exact=True).uncheck();await row.get_by_role('button',name='Save',exact=True).click()
    await page.wait_for_function('() => !document.querySelector("[data-crew-row=\\\"1\\\"] input[aria-label=\\\"Race Engineer\\\"]").checked')
    assert fixture.writes and fixture.writes[-1]['race_engineer'] is False
    await row.get_by_label('Race Engineer',exact=True).check();await row.get_by_role('button',name='Save',exact=True).click()
    for _ in range(50):
        if 'engineer' in fixture.crew.race_engineer_keys():break
        await asyncio.sleep(.05)
    assert 'engineer' in fixture.crew.race_engineer_keys()
    engineer=await browser.new_page(viewport={'width':1280,'height':900})
    engineer.on('pageerror',lambda error:errors.append(error.stack))
    token=gate.m.console_auth.mint_token(gate.SECRET,'engineer')
    base='http://127.0.0.1:'+str(console)
    await engineer.goto(base+'/console/race-engineer?t='+token)
    await engineer.wait_for_selector('#protected:not([hidden])')
    assert fixture.source.name not in await engineer.locator('body').inner_text()
    assert await engineer.locator('#references li').count()==1
    assert not await engineer.evaluate('() => !!window.engineerInjected')
    fixture.access().update(revoke=fixture.source.identity,expected=2)
    await engineer.wait_for_function('() => document.querySelectorAll("#references li").length===0')
    fixture.crew.inject_row(1,race_engineer=False)
    await engineer.wait_for_function('() => document.getElementById("protected").hidden')
    assert 'capability' in await engineer.locator('#status').inner_text()
    fixture.crew.inject_row(1,race_engineer=True)
    await engineer.goto(base+'/console/race-engineer?t='+token)
    await engineer.wait_for_selector('#protected:not([hidden])')
    fixture.access().update(enabled=False,expected=3)
    await engineer.wait_for_function('() => document.getElementById("protected").hidden')
    assert 'disabled' in await engineer.locator('#status').inner_text()
    # A delayed producer response cannot restore the previous profile's grants.
    await page.evaluate('() => showView("telemetry")')
    await page.wait_for_function('() => !document.getElementById("engineer-save").disabled')
    captured=asyncio.Event();release=asyncio.Event()
    async def delayed(route):
        response=await route.fetch();data=await response.json();captured.set();await release.wait()
        await route.fulfill(json=data)
        return None
    await page.route('**/api/engineer?*',delayed)
    pending=asyncio.create_task(page.evaluate('() => RacecastEngineer.settings()'))
    await captured.wait();await page.evaluate('() => useProfile("other")')
    await page.wait_for_function('() => tmState.profile==="other"')
    release.set();await pending;await page.unroute_all(behavior='wait')
    assert await page.locator('#engineer-grants li').count()==0
    await page.evaluate('() => useProfile("profile")')
    await page.wait_for_function('() => tmState.profile==="profile"')
    if shots:
        fixture.access().update(enabled=True,grant=fixture.source.identity,label='Practice reference',expected=4)
        await engineer.goto(base+'/console/race-engineer?t='+token)
        await engineer.wait_for_selector('#protected:not([hidden])')
        await engineer.screenshot(path=str(ROOT/'src/docs/wiki/images/console-race-engineer-access.png'))
        await engineer.goto(base+'/console?t='+gate.m.console_auth.mint_token(gate.SECRET,'producer'));await engineer.wait_for_selector('a[href="/console/race-engineer"]')
        await engineer.screenshot(path=str(ROOT/'src/docs/wiki/images/console-landing.png'))
        await page.evaluate('() => {showView("profile");loadCrew();}');await page.wait_for_selector('[data-crew-row="1"]')
        await page.locator('#crew-rows').evaluate('(node) => node.parentElement.scrollIntoView({block:"center"})')
        await page.screenshot(path=str(ROOT/'src/docs/wiki/images/cc-crew-editor.png'))
        await page.evaluate('() => showView("telemetry")');await page.wait_for_function('() => !document.getElementById("engineer-save").disabled')
        await page.locator('[data-view="telemetry"] .viewhead').scroll_into_view_if_needed()
        await page.screenshot(path=str(ROOT/'src/docs/wiki/images/cc-telemetry.png'))
    assert not errors,errors
    await browser.close()
    print('PASS producer grants/role roundtrip, signed Console, XSS, reference/role/feature revocation')
    return None


async def main():
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix='racecast-engineer-browser-') as root:
        fixture=Fixture(root);server,port=web_fixture._serve(fixture.context());console=fixture.console()
        try:
            async with async_playwright() as pw:await verify(pw,fixture,port,console.server_port,'--shots' in sys.argv)
        finally:
            server.shutdown();server.server_close();console.shutdown();console.server_close()
    return None


if __name__=='__main__':asyncio.run(main())

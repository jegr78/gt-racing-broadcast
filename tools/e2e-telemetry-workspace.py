#!/usr/bin/env python3
"""Production local workspace/CLI against isolated profiles, without a live relay."""
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import e2e


async def verify(base, home, launcher, env, shots):
    from playwright.async_api import async_playwright
    pointer = Path(home) / 'runtime/active-profile'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={'width':1440, 'height':1050})
        errors = []
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(base + '/telemetry?profile=alpha')
        await page.wait_for_function('() => tmState.loaded && !!tmState.lapB?.recording_id')
        assert await page.locator('[data-view="archive"]').is_visible()
        assert await page.locator('.archive-card').count() == 1
        assert pointer.read_text(encoding='utf-8').strip() == 'alpha'
        other = await context.new_page()
        other.on('pageerror', lambda error: errors.append(str(error)))
        await other.goto(base + '/telemetry?profile=beta')
        await other.wait_for_function('() => tmState.loaded && !!tmState.lapB?.recording_id')
        await other.locator('.archive-card').click()
        await other.wait_for_function('() => currentView === "telemetry" && !!tmState.chart')
        assert await page.evaluate('() => tmState.profile') == 'alpha'
        assert await other.evaluate('() => tmState.profile') == 'beta'
        assert pointer.read_text(encoding='utf-8').strip() == 'alpha'
        assert await other.locator('#workspace-broadcast').inner_text() == 'Broadcast profile: alpha'
        assert await other.locator('#tm-charts').bounding_box() is not None
        assert not await other.locator('#workspace-inspector').is_visible()
        await other.locator('#workspace-details').click()
        assert await other.locator('#workspace-inspector').is_visible()
        await other.locator('#workspace-start').fill('100')
        await other.locator('#workspace-end').fill('500')
        await other.get_by_role('button', name='Apply zoom', exact=True).click()
        assert await other.evaluate('() => [tmState.chart.minD,tmState.chart.maxD]') == [100,500]
        await other.locator('#workspace-cursor').focus()
        await other.locator('#workspace-cursor').press('Home')
        for _ in range(40):await other.locator('#workspace-cursor').press('ArrowRight')
        assert await other.locator('#workspace-distance').inner_text() == '300 m'
        assert await other.evaluate('() => Number(tmState.cursor.getAttribute("x1")) > 0')
        await other.get_by_role('button', name='Full lap', exact=True).click()
        assert await other.evaluate('() => tmState.chart.minD') == 0
        await other.get_by_role('button', name='Notes & context', exact=True).click()
        await other.wait_for_function('() => tc.doc && !tc.loading')
        await other.get_by_label('Race / test name', exact=True).fill('Beta-only context')
        await other.evaluate('() => tcSave()')
        await other.get_by_role('button', name='Close', exact=True).click()
        beta = Path(home)/'runtime/beta/telemetry-recordings'
        alpha = Path(home)/'runtime/alpha/telemetry-recordings'
        assert any('Beta-only context' in p.read_text(encoding='utf-8') for p in beta.glob('*.context.json'))
        assert not any('Beta-only context' in p.read_text(encoding='utf-8') for p in alpha.glob('*.context.json'))
        # A profile change during Delete confirmation must never delete a same-named source.
        await other.locator('#tm-del').click()
        await other.evaluate('() => workspaceProfile("alpha")')
        await other.locator('#cmodal-ok').click()
        await other.wait_for_timeout(200)
        assert len(list(alpha.glob('*.gt7rec'))) == len(list(beta.glob('*.gt7rec'))) == 1
        await other.locator('#workspace-profile').select_option('beta')
        await other.wait_for_function('() => tmState.profile === "beta" && !!tmState.lapB?.recording_id')
        await other.get_by_role('button', name='Data management', exact=True).first.click()
        assert 'shared across all profiles' in await other.locator('[data-view="settings"]').inner_text()
        await other.get_by_role('button', name='Export CSV', exact=True).click()
        await other.wait_for_function('() => document.getElementById("workspace-status").textContent.includes("wrote ")')
        assert list(beta.glob('*/samples.csv')) and list(beta.glob('*/laps.csv'))
        assert not list(alpha.glob('*/samples.csv'))
        assert pointer.read_text(encoding='utf-8').strip() == 'alpha'
        result = subprocess.run(launcher + ['--profile','beta','telemetry','ui','--no-browser'],env=env,cwd=home,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
        assert result.returncode == 0 and 'already running' in result.stdout, result.stdout+result.stderr
        assert pointer.read_text(encoding='utf-8').strip() == 'alpha'
        response = await context.request.get(base+'/')
        assert 'id="tm-charts"' not in await response.text()
        if not await other.locator('#workspace-inspector').is_hidden():await other.evaluate('() => workspaceInspector()')
        for width in (1440,1024,390):
            await other.set_viewport_size({'width':width,'height':1050})
            for view in ('archive','telemetry','settings','reports'):
                await other.evaluate('name => showView(name)',view)
                await other.wait_for_timeout(100)
                assert await other.evaluate('() => document.documentElement.scrollWidth <= innerWidth && document.querySelector("main").scrollWidth <= document.querySelector("main").clientWidth'), (width,view)
                if shots and width==1440:
                    label={'telemetry':'analysis','settings':'data'}.get(view,view)
                    await other.screenshot(path=str(ROOT/'src/docs/wiki/images'/('telemetry-'+label+'.png')))
                    if view=='telemetry':await other.locator('.tmmain').screenshot(path='/tmp/racecast-887-analysis-element.png')
        await other.reload()
        await other.wait_for_function('() => tmState.loaded && !!tmState.profile')
        assert await other.locator('#workspace-last').is_visible()
        await other.locator('#workspace-last').click()
        await other.wait_for_function('() => currentView === "telemetry" && !!tmState.lapB?.recording_id')
        await page.close()
        assert (await context.request.get(base+'/api/ping')).status == 200
        assert not errors, errors
        await browser.close()
    print('PASS workspace: independent profiles/notes/jobs, delayed Delete, shared launcher, zoom/cursor, reload, offline archive and responsive tasks')
    return None


async def main():
    with tempfile.TemporaryDirectory(prefix='racecast-workspace-') as temp:
        launcher,home=e2e._pov_home(temp,None)
        e2e._seed_telemetry(home)
        home=Path(home)
        shutil.move(str(home/'profiles/solo-pov'),str(home/'profiles/alpha'))
        shutil.copytree(home/'profiles/alpha',home/'profiles/beta')
        shutil.move(str(home/'runtime/solo-pov'),str(home/'runtime/alpha'))
        shutil.copytree(home/'runtime/alpha',home/'runtime/beta')
        (home/'runtime/active-profile').write_text('alpha\n',encoding='utf-8')
        port=e2e.E.free_port()
        env=dict(os.environ,RACECAST_UI_PORT=str(port),PYTHONUNBUFFERED='1')
        env.pop('RACECAST_PROFILE',None)
        # Actual CLI/server/store paths; only relay-status I/O is replaced with offline.
        source=str(home/'src')
        child="import sys;sys.path.insert(0,"+repr(source)+");import racecast as rc;rc._relay_record_status=lambda:None;rc.main()"
        code=("import sys;sys.path.insert(0,sys.argv[1]);import racecast as rc;"
              "rc._relay_record_status=lambda:None;"
              "mods=rc._ui_modules();mods[2].job_argv=lambda args,*unused:[sys.executable,'-c',"+repr(child)+",*args];"
              "sys.argv=['racecast',*sys.argv[2:]];rc.main()")
        log=home/'workspace.log'
        proc=e2e._spawn([sys.executable,'-c',code,source,'telemetry','ui','--no-browser'],env,str(log),cwd=str(home))
        try:
            base='http://127.0.0.1:'+str(port)
            e2e._wait_ready(base+'/api/ping',30,proc,str(log))
            await verify(base,str(home),launcher,env,'--shots' in sys.argv)
        except Exception:
            print(log.read_text(encoding='utf-8',errors='replace'))
            raise
        finally:
            e2e._kill(proc)
    return None


if __name__=='__main__':asyncio.run(main())

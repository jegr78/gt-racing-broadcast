#!/usr/bin/env python3
"""Drive operator controls with real HTTP and fake jobs and devices."""
import argparse
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests'),str(ROOT/'src'),str(ROOT/'src/ui')]
import test_ui_server as ui
from test_driver_radio_ui import RadioFixture
import racecast as rc
import ui_jobs
import ui_ops
import e2e_safety


def main():
    contained=e2e_safety.enter(__file__,sys.argv[1:])
    if contained is not None:return contained
    parser=argparse.ArgumentParser()
    parser.add_argument('--shots',action='store_true')
    parser.add_argument('--browser-executable')
    args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    state={'profile':'demo'};calls=[];inputs=[];errors=[]
    class Process:
        def __init__(self,argv,input_text=None):
            calls.append(argv);inputs.append(input_text)
            self.stdout=io.BytesIO(b'Synthetic operator action completed.\n');self.returncode=None
        def wait(self):self.returncode=0;return 0
        def terminate(self):raise AssertionError('No synthetic action should need termination')
    jobs=ui_jobs.JobManager(list,spawn=Process)
    def profiles():
        return {'ok':True,'active':state['profile'],'profiles':[
            {'name':'demo','display':'Demo Racing','kind':'endurance'},
            {'name':'solo-pov','display':'Solo POV demo','kind':'solo','template':'pov'}]}
    def use_profile(name,force=False):
        assert name in ('demo','solo-pov');state['profile']=name
        return {'ok':True,'active':name}
    with RadioFixture(coaching=True) as radio,patch.object(rc,'_active_profile_name',lambda:state['profile']):
        ctx=ui._ctx(jobs=jobs)
        ctx.update(version='dev build',ops=ui_ops.OPS,build_argv=ui_ops.build_argv,
            profile_ops=ui_ops.PROFILE_OPS,active_profile=lambda:state['profile'],
            profiles=profiles,profile_use=use_profile,driver_radio=radio.admin,
            status=lambda:{'ok':True,'version':'dev build','os':'linux','relay':{'alive':False,'http_ok':False,'port':8088},
                'streams':[],'companion':{'running':False,'url':''},'apps_running':{},'tailscale_ip':None},
            apps=lambda:{'ok':True,'apps':[{'name':name,'installed':True,'version':'demo'}
                for name in ('obs','companion','discord','tailscale')]},
            update_check=lambda force=False:{'ok':True,'update_available':False,'note':'development build'})
        server,port=ui._serve(ctx)
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=args.browser_executable,
                    args=['--renderer-process-limit=2','--num-raster-threads=1'])
                try:
                    page=browser.new_page(viewport={'width':1440,'height':900})
                    page.on('pageerror',lambda error:errors.append(error.stack))
                    page.goto(f'http://127.0.0.1:{port}/')
                    page.wait_for_function("() => activeProfile==='demo' && lastStatus?.os==='linux'")
                    page.evaluate("""() => {window.confirmed=[];window.alerts=[];window.allowConfirm=true;
                        window.confirmModal=async message=>{confirmed.push(message);return allowConfirm;};
                        window.alertModal=async message=>{alerts.push(message);};
                        window.promptAnswers=[];window.prompt=()=>promptAnswers.shift()??null;}""")
                    def view(name):
                        page.evaluate('(name)=>showView(name)',name)
                        page.locator(f'[data-view="{name}"]').wait_for(state='visible')
                    def click_job(selector,expected,prompts=None,with_replay_probe=False):
                        before=len(calls)
                        page.evaluate('(values)=>{promptAnswers=values;}',prompts or [])
                        with page.expect_response(lambda response:'/api/op/' in response.url and not response.url.endswith('/telemetry-replay-status')) as response:
                            if with_replay_probe:
                                page.wait_for_function('!tmReplayBusy')
                                page.evaluate('tmReplayPoll()')
                                page.wait_for_function('!tmReplayBusy')
                            (page.locator(selector) if isinstance(selector,str) else selector).click()
                        result=response.value.json();assert result['ok'],result
                        page.wait_for_function("id=>jobId===id && !document.getElementById('jobchip').hidden && document.getElementById('jobchip').textContent==='done'",arg=result['job_id'])
                        # Replay status polls are read-only background work, not operator submissions.
                        submitted=[argv for argv in calls[before:] if argv!=['telemetry','replay','status']]
                        assert submitted==[expected],(selector,submitted,expected)
                    view('tools')
                    page.evaluate('allowConfirm=false')
                    page.locator('#op-benchmark').click()
                    assert calls==[] and page.evaluate('confirmed.length')==1
                    page.evaluate('allowConfirm=true')
                    page.locator('#op-benchmark-window').fill('30');page.locator('#op-benchmark-settle').fill('3')
                    click_job('#op-benchmark',['--profile','demo','obs','benchmark','--window','30','--settle','3'])
                    before=len(calls)
                    page.evaluate("promptAnswers=['wrong phrase']")
                    with page.expect_response(lambda response:response.url.endswith('/api/op/smoketest')) as refused:
                        page.locator('#op-smoketest').click()
                    assert refused.value.status==400 and not refused.value.json()['ok'] and len(calls)==before
                    click_job('#op-smoketest',['--profile','demo','smoketest','--minutes','2'],['CLEAR SCHEDULE demo'])
                    assert inputs[-1]=='CLEAR SCHEDULE demo\n'
                    view('services')
                    click_job('#op-chat-pull',['--profile','demo','chat','pull','100.64.0.2','--port','8088'],['100.64.0.2','8088'])
                    click_job('#op-chat-import',['--profile','demo','chat','import','saved chat.json'],['saved chat.json'])
                    click_job('#op-chat-export',['--profile','demo','chat','export','--out','chat export.json'],['chat export.json'])
                    click_job('#op-health-pull',['--profile','demo','health','pull','100.64.0.2','--port','8088'],['100.64.0.2','8088'])
                    before=len(calls);page.evaluate("activeProfile='old';promptAnswers=['stale.json']")
                    page.locator('#op-chat-import').click()
                    page.wait_for_function("() => alerts.some(value=>value.includes('Active league changed'))")
                    assert len(calls)==before
                    page.evaluate("activeProfile='demo'")
                    for label,verb in [('Director panel','open-panel'),('HUD','open-hud'),('Relay status','open-status')]:
                        click_job('[data-view=services] button:has-text("'+label+'")',['relay',verb])
                    view('streams');click_job('#op-streams-restart',['--profile','demo','streams','restart'])
                    view('console')
                    click_job('#op-funnel-plan',['console','setup-funnel'])
                    click_job('#op-funnel-apply',['console','setup-funnel','--apply'])
                    click_job('#op-console-pull-versions',['--profile','demo','console','pull-versions','100.64.0.2','--port','8088'],['100.64.0.2','8088'])
                    view('apps')
                    page.locator('#linux-admin-actions').wait_for(state='visible')
                    row=page.locator('.item').filter(has=page.locator('.iname',has_text='Companion'))
                    for label,verb in [('Restart','restart'),('Web buttons','open-buttons'),('Admin','open-admin')]:
                        click_job(row.get_by_role('button',name=label,exact=True),['companion',verb])
                    click_job('#op-companion-control',['companion','enable-control'])
                    click_job('#op-obs-browser',['obs-browser','--yes'])
                    assert sum(value is not None for value in inputs)==1,inputs
                    page.evaluate("useProfile('solo-pov')")
                    page.wait_for_function("() => activeProfile==='solo-pov'")
                    view('telemetry')
                    for verb in ('start','stop','status'):
                        click_job('#op-record-'+verb,['--profile','solo-pov','telemetry','record',verb],with_replay_probe=True)
                    if args.shots:
                        page.evaluate("useProfile('demo')")
                        page.wait_for_function("() => activeProfile==='demo'")
                        page.evaluate('toggleConsole(false)')
                        for name,file in (('services','cc-relay'),('streams','cc-streams'),('apps','cc-apps'),
                                          ('tools','cc-tools'),('console','cc-crew-console')):
                            view(name)
                            page.wait_for_timeout(150)
                            page.screenshot(path=str(ROOT/'src/docs/wiki/images'/(file+'.png')))
                        page.evaluate("useProfile('solo-pov')")
                        page.wait_for_function("() => activeProfile==='solo-pov'")
                        view('telemetry')
                        page.wait_for_function("() => window.radioAdmin?.coach?.state && document.getElementById('driver-radio-admin').shadowRoot?.querySelector('#content')?.hidden===false")
                        page.screenshot(path=str(ROOT/'src/docs/wiki/images/cc-telemetry.png'))
                        page.locator('[aria-label="Telemetry recording controls"]').scroll_into_view_if_needed()
                        page.screenshot(path=str(ROOT/'src/docs/wiki/images/cc-telemetry-recording.png'))
                    assert not errors,errors
                    print(json.dumps({'ok':True,'jobs':len(calls),'browserErrors':errors}))
                finally:browser.close()
        finally:server.shutdown();server.server_close()
    return 0


if __name__=='__main__':raise SystemExit(main())

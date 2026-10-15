#!/usr/bin/env python3
"""Workspace startup routing and explicit profile resets, exercised under Node."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def harness():
    source = (ROOT / 'src/ui/telemetry-shell.js').read_text(encoding='utf-8')
    show = source[source.index('function showView('):source.index('let modalResolve')]
    profile = source[source.index('async function workspaceProfile('):source.index('function workspaceInspector(')]
    init = source[source.index('async function workspaceInit('):]
    return r'''
const tmState={profile:null},events=[],els={};let currentView='archive';
const location={search:'?profile=alpha',hash:'#reports'};
const views=['archive','telemetry','reports','settings'].map(view=>({dataset:{view},hidden:false}));
const navs=views.map(view=>({dataset:{nav:view.dataset.view},classList:{toggle(){}}}));
const $=id=>{if(['ai-analysis','ai-feedback'].includes(id))return null;return els[id]||(els[id]={append(){},addEventListener(){},textContent:'',value:''});};
const document={querySelectorAll:selector=>selector==='[data-view]'?views:navs};
const history={replaceState(_a,_b,url){location.hash=url.slice(url.indexOf('#'));}};
const window={RacecastAI:{reset(){},settings(){events.push('settings');},status(){events.push('status');},history(){events.push('history');}}};
const Option=function(label,value){return {label,value};};
const tmEditorClose=()=>{},tmReset=()=>{tmState.profile=null;},tmLoad=async()=>{},workspaceSelection=()=>{},workspaceStatus=()=>{};
const requestAnimationFrame=fn=>fn(),tmRefit=()=>{};
const tmOpenReference=async ref=>{events.push(ref);if(ref.recording_id!=='valid')return false;showView('telemetry');return true;};
const workspaceFetch=async url=>({json:async()=>url==='/api/ping'?{version:'dev'}:{ok:true,active:'alpha',profiles:[{name:'alpha'},{name:'beta'}]}});
''' + show + profile + init


def run(body):
    executable = shutil.which('node')
    if not executable:
        print('SKIP: node unavailable')
        return None
    result = subprocess.run([executable, '-'], input=harness() + body, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', check=True, timeout=15)
    return result.stdout.strip()


def t_startup_preserves_each_supported_view_and_shows_its_section():
    for view in ('archive', 'telemetry', 'reports', 'settings'):
        out = run("location.hash='#" + view + "';workspaceInit().then(()=>console.log(currentView,location.hash,views.filter(v=>!v.hidden).map(v=>v.dataset.view).join(',')));")
        if out is not None:
            assert out == f'{view} #{view} {view}', out


def t_unknown_view_falls_back_to_archive():
    out = run("location.hash='#unknown';workspaceInit().then(()=>console.log(currentView,location.hash));")
    if out is not None:
        assert out == 'archive #archive', out


def t_explicit_profile_switch_returns_to_archive():
    out = run("workspaceInit().then(async()=>{console.log(currentView);await workspaceProfile('beta');console.log(tmState.profile,currentView,location.hash);});")
    if out is not None:
        assert out.splitlines() == ['reports', 'beta archive #archive'], out


def t_recording_evidence_query_keeps_priority_and_validation():
    for identity, expected in (('valid', 'telemetry'), ('invalid', 'archive')):
        out = run("location.search+='&rec=recording&session=1&lap=2&recording_id=" + identity + "&location_m=500';workspaceInit().then(()=>{const ref=events.find(e=>typeof e==='object');console.log(currentView,ref.profile,ref.rec,ref.lap,ref.location_m);});")
        if out is not None:
            assert out == expected + ' alpha recording 2 500', out


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('t_') and callable(fn):
            fn()
            print('PASS', name)

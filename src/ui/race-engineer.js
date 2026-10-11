/* Producer-only access controls. Reference identities never authorize an archive browse. */
(() => {
  'use strict';
  const box=document.createElement('section');box.className='ai-card';box.id='engineer-settings';
  box.innerHTML='<h3>Race Engineer access</h3><p>Allow assigned Engineers to inspect this solo POV session through their Console. Shared historical references include their analysis context and notes. Previously transmitted data cannot be recalled.</p><label><input id="engineer-enabled" type="checkbox"> Allow Race Engineer</label> <button id="engineer-save" type="button">Save access</button><button id="engineer-reload" type="button">Reload access settings</button><p id="engineer-status" role="status"></p><div id="engineer-controls" hidden><label>Historical reference <select id="engineer-recordings" aria-label="Historical reference recording"></select></label> <label>Shared label <input id="engineer-label" maxlength="160" placeholder="Shared telemetry reference"></label> <button id="engineer-grant" type="button">Share recording and context</button><ul id="engineer-grants"></ul></div>';
  document.querySelector('[data-view="telemetry"] .viewhead').after(box);
  const el=id=>document.getElementById(id);
  const state={profile:null,generation:0,revision:0};
  function clear() {
    el('engineer-recordings').replaceChildren();el('engineer-grants').replaceChildren();
    el('engineer-enabled').disabled=true;el('engineer-save').disabled=true;el('engineer-grant').disabled=true;
    el('engineer-enabled').checked=false;el('engineer-controls').hidden=true;el('engineer-label').value='';
  }
  async function request(change) {
    const profile=state.profile,generation=++state.generation;
    if (!profile || profile!==tmState.profile) return;
    if(change){el('engineer-save').disabled=true;el('engineer-grant').disabled=true;}
    try {
      const response=await fetch('/api/engineer'+(change?'':'?profile='+encodeURIComponent(profile)),change?{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...change,profile,expected:state.revision})}:{});
      const data=await response.json();
      if (generation!==state.generation || profile!==tmState.profile) return;
      if (!data.ok || data.profile!==profile) throw new Error(data.error?.message || 'Profile changed; reload Engineer settings');
      state.revision=data.revision;clear();
      el('engineer-enabled').checked=data.enabled;el('engineer-enabled').disabled=!data.available;el('engineer-save').disabled=!data.available;
      el('engineer-status').textContent=!data.available?'Unavailable: Race Engineer requires a solo POV telemetry profile.':data.enabled?(data.analysis_available===false?'Engineer access is enabled. Live analysis is unavailable in this version.':'Assigned Engineers can read this session.'):'Engineer access is disabled.';
      el('engineer-controls').hidden=!data.available;
      for (const recording of data.recordings) {
        const option=document.createElement('option');option.value=recording.id;option.textContent=recording.name;el('engineer-recordings').append(option);
      }
      el('engineer-grant').disabled=!data.available || !data.recordings.length;
      for (const grant of data.grants) {
        const row=document.createElement('li'),text=document.createElement('span'),button=document.createElement('button');
        text.textContent=grant.label+' — recording and analysis context shared ';button.type='button';button.textContent='Revoke';
        button.onclick=()=>request({revoke:grant.id});row.append(text,button);el('engineer-grants').append(row);
      }
    } catch(error) {
      if (generation!==state.generation || profile!==tmState.profile) return;
      clear();el('engineer-status').textContent=error.message;
    }
  }
  function reset(profile) {
    state.profile=profile;state.generation++;clear();el('engineer-status').textContent='';
    if (profile && currentView==='telemetry') request();
  }
  el('engineer-reload').onclick=()=>request();
  el('engineer-save').onclick=()=>request({enabled:el('engineer-enabled').checked});
  el('engineer-grant').onclick=()=>request({grant:el('engineer-recordings').value,label:el('engineer-label').value.trim() || null});
  window.RacecastEngineer={reset,settings:()=>request()};reset(tmState.profile);
})();

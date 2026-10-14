/* Local archive workspace. Each tab owns its analysis profile and selection. */
'use strict';
const $ = id => document.getElementById(id);
let currentView = 'archive';
const workspaceFetch = window.fetch.bind(window);
window.fetch = (input, options) => {
  const url = new URL(input instanceof Request ? input.url : input, location.href);
  if (url.origin === location.origin && url.pathname.startsWith('/api/') &&
      (url.pathname.startsWith('/api/telemetry/') || url.pathname.startsWith('/api/ai/') ||
       url.pathname.startsWith('/api/op/') || url.pathname.startsWith('/api/jobs/')) && tmState.profile) {
    if (!url.searchParams.has('profile')) url.searchParams.set('profile', tmState.profile);
    return workspaceFetch(input instanceof Request ? new Request(url.href,input) : url.href, options);
  }
  return workspaceFetch(input, options);
};
function escapeHtml(text) {
  const node = document.createElement('span'); node.textContent = text; return node.innerHTML;
}
function workspaceStatus(message, error=true) {
  $('workspace-status').className=error?'enverr':'sub';$('workspace-status').textContent = message?.message || message || ''; $('workspace-status').hidden = !message;
}
function showView(name) {
  currentView = name;
  if($('workspace-status').className==='sub')workspaceStatus('');
  document.querySelectorAll('[data-view]').forEach(el => el.hidden = el.dataset.view !== name);
  document.querySelectorAll('[data-nav]').forEach(el => el.classList.toggle('active', el.dataset.nav === name));
  if (name === 'telemetry') requestAnimationFrame(() => tmRefit());
  if (name === 'settings') window.RacecastAI?.settings();
  if (name === 'reports') {window.RacecastAI?.status(); window.RacecastAI?.history();}
  history.replaceState(null, '', '/telemetry?'+new URLSearchParams({profile: tmState.profile || ''})+'#'+name);
}
let modalResolve = null;
function customModal(options) {
  if (modalResolve) {modalResolve(false); modalResolve = null;}
  $('cmodal-title').textContent = options.title || 'Please confirm';
  $('cmodal-body').textContent = options.body || '';
  $('cmodal-ok').textContent = options.okLabel || 'OK';
  $('cmodal-cancel').hidden = !!options.isAlert;
  const dialog = $('cmodal'); dialog.returnValue = ''; dialog.showModal();
  return new Promise(resolve => {modalResolve = resolve;});
}
function confirmModal(body, options) {return customModal({body, ...options});}
function alertModal(body, options) {return customModal({body, isAlert: true, ...options});}
async function startJob(name, params) {
  try {
    const response = await fetch('/api/op/'+name, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({params:params || {}})});
    const data = await response.json();
    return data.ok ? {job_id:data.job_id} : {error:data.error?.message || data.error || 'Job failed', job_id:data.job_id};
  } catch (_error) {return {error:'Local UI service not reachable.'};}
}
async function jobResult(id, tail) {
  const profile = tmState.profile;
  for (;;) {
    if (profile !== tmState.profile) return {error:'Analysis profile changed.'};
    try {
      const response = await workspaceFetch('/api/jobs/'+id+'?'+new URLSearchParams({tail:tail || 0, profile}));
      const data = await response.json();
      if (!data.ok) return {error:data.error?.message || data.error || 'Job unavailable'};
      if (!data.running) return {code:data.exit_code, lines:data.lines || []};
    } catch (_error) {return {error:'Local UI service not reachable.'};}
    await new Promise(resolve => setTimeout(resolve, 400));
  }
}
function workspaceArchive() {
  const box = $('workspace-archive'); box.replaceChildren();
  for (const row of tmState.recs) {
    const button = document.createElement('button'); button.className = 'archive-card';
    if (row.rec === tmState.rec) button.classList.add('selected');
    const title = document.createElement('strong'); title.textContent = row.started ? new Date(row.started).toLocaleString() : row.name;
    const track = document.createElement('span'); track.className = 'sub'; track.textContent = tmTrackLabel(row.track) || 'Track not assigned';
    const info = document.createElement('span'); info.className = 'sub';
    info.textContent = row.recording ? 'Recording in progress · analysis available after capture stops' :
      (row.completed_laps ?? row.laps ?? 'Unindexed')+' completed laps · '+Math.round(row.duration_s || 0)+' s · '+(Number.isFinite(row.size)?(row.size/1048576).toFixed(1):'unknown')+' MB';
    button.append(title,track,info); button.disabled = !!row.recording;
    button.onclick = async () => {await tmSelectRec(row.rec); showView('telemetry'); workspaceSelection();};
    box.append(button);
  }
  if (!tmState.recs.length) {
    const empty = document.createElement('p'); empty.className = 'workspace-empty';
    empty.textContent = 'No recordings in this profile. Import a .gt7rec file or record a session with the relay.'; box.append(empty);
  }
  const saved = workspaceSaved(); $('workspace-last').hidden = !saved;
}
function workspaceSaved() {
  try {return JSON.parse(localStorage.getItem('racecast-telemetry-comparison:'+tmState.profile));}
  catch (_error) {return null;}
}
function workspaceSelection() {
  const lap = tmState.lapB;
  $('workspace-selected').textContent = tmState.rec ? tmState.profile+' · '+tmState.rec+(lap ? ' · '+tmLapTrack(lap)+' · vehicle '+(lap.car_id || 'unknown') : '') : 'Choose a recording in Archive.';
  if (currentView === 'telemetry' && lap?.recording_id) {
    const reference = tmState.lapA;
    try {localStorage.setItem('racecast-telemetry-comparison:'+tmState.profile, JSON.stringify({profile:tmState.profile, rec:lap.rec, session:lap.session, lap:lap.lap, recording_id:lap.recording_id,
      included:[...(tmState.included||[])],compare_all:$('tm-compare-all').checked,reference:reference?.recording_id ? {rec:reference.rec,session:reference.session,lap:reference.lap,recording_id:reference.recording_id}:null}));}
    catch (_error) {/* Storage may be disabled; archive analysis remains available. */}
  }
}
async function workspaceLast() {
  const saved = workspaceSaved(); if (!saved) return;
  if (!await tmOpenReference(saved)) return;
  const available=new Set(tmState.recs.map(row=>row.rec));
  tmState.included=new Set((saved.included||[saved.rec,saved.reference?.rec]).filter(name=>available.has(name)));
  $('tm-compare-all').checked=!!saved.compare_all;
  await tmReloadRecordingPool();
  if(saved.profile!==tmState.profile)return;
  const reference=saved.reference;
  const key=reference && tmState.pool?.laps?.find(row=>row.rec===reference.rec && row.session===reference.session && row.lap===reference.lap);
  if(key && [...$('tm-a').options].some(option=>option.value===tmKey(key))){
    await tmPick('a',tmKey(key));
    if(tmState.lapA?.recording_id===reference.recording_id)return;
  }
  tmState.a=null;await tmLoadPair();
  if(reference)tmErr('The saved reference is unavailable or changed. Select a new reference.');
}

async function workspaceProfile(name) {
  if (!name || name === tmState.profile) return;
  if (typeof tc !== 'undefined' && tc.open) {await tcClose(); if (tc.open) {$('workspace-profile').value=tmState.profile;return;}}
  tmEditorClose(); tmReset(); tmState.profile=name; tmState.zoom=null;
  window.RacecastAI?.reset(name); workspaceStatus('');
  $('workspace-start').value=''; $('workspace-end').value='';
  showView('archive'); await tmLoad(); workspaceSelection();
}
function workspaceInspector() {
  const visible = $('workspace-inspector').hidden;
  $('workspace-inspector').hidden = !visible;
  $('workspace-details').setAttribute('aria-expanded', String(visible));
  document.querySelector('.tmgrid').classList.toggle('has-inspector', visible);
  requestAnimationFrame(() => tmRefit());
}
function workspaceAI() {
  showView('telemetry'); $('workspace-ai').open=true; $('workspace-ai').scrollIntoView({block:'start'});
}
function workspaceZoom(reset=false) {
  if (!tmState.lapB) return;
  const max = Math.max(...tmTrace(tmState.lapB).map(row => row.d));
  const start=Number($('workspace-start').value || 0), end=Number($('workspace-end').value || max);
  if (!reset && (!Number.isFinite(start) || !Number.isFinite(end) || start<0 || end>max || start>=end)) {tmErr('Select a distance window within the analysed lap.');return;}
  tmState.zoom=reset?null:{start,end}; tmErr('');
  if(reset){$('workspace-start').value='';$('workspace-end').value='';}
  tmRenderCharts();tmRenderMap();workspaceSeek(Number($('workspace-cursor').value));
}
function workspaceSeek(distance) {
  const chart=tmState.chart;if(!chart)return;
  const box=$('tm-charts').getBoundingClientRect();
  tmHover({clientX:box.left+chart.X(distance)/chart.W*box.width});
}
function workspaceCursor(distance) {
  $('workspace-cursor').value=String(distance);$('workspace-distance').textContent=Math.round(distance)+' m';
}
async function workspaceExport() {
  const row=tmState.recs.find(item=>item.rec===tmState.rec);if(!row||row.recording)return;
  const profile=tmState.profile;
  const job=await startJob('telemetry-export',{name:row.name});
  const result=job.error?job:await jobResult(job.job_id,20);
  if(profile!==tmState.profile)return;
  workspaceStatus(result.error || (result.code!==0?'Export failed: ':'')+(result.lines||[]).join('\n'), !!result.error || result.code!==0);
}
async function workspaceQuit() {
  if (!await confirmModal('Stop the shared UI service? Control Center and Telemetry will disconnect. The relay and broadcast keep running.',{title:'Stop UI service',okLabel:'Stop UI service'})) return;
  let response=await workspaceFetch('/api/quit',{method:'POST'}),data=await response.json();
  if(response.status===409){if(!await confirmModal(data.error.message,{title:'Active AI analysis',okLabel:'Interrupt analysis and stop'}))return;response=await workspaceFetch('/api/quit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});data=await response.json();}
  if(!data.ok){workspaceStatus(data.error?.message||'Could not stop the UI service.');return;}
  document.body.replaceChildren();const message=document.createElement('p');message.textContent='UI service stopped. You can close this tab.';document.body.append(message);
}
async function workspaceInit() {
  $('cmodal').addEventListener('close',()=>{const resolve=modalResolve;modalResolve=null;resolve?.($('cmodal').returnValue==='ok');});
  const ai=$('ai-analysis');
  if(ai){const details=document.createElement('details');details.id='workspace-ai';const summary=document.createElement('summary');summary.textContent='Post-session AI analysis';details.append(summary,ai);document.querySelector('[data-view="telemetry"]').append(details);
    for(const id of ['ai-history','ai-report'])$('workspace-reports').append($(id));
  }
  const ping=await (await workspaceFetch('/api/ping')).json();$('ver').textContent=ping.version==='dev'?'dev build':ping.version;
  const query=new URLSearchParams(location.search),response=await workspaceFetch('/api/profiles'),data=await response.json();
  if(!data.ok){workspaceStatus(data.error||'Profiles unavailable.');return;}
  const profiles=data.profiles||[];
  for(const profile of profiles)$('workspace-profile').append(new Option(profile.display&&profile.display!==profile.name?profile.display+' ('+profile.name+')':profile.name,profile.name));
  $('workspace-broadcast').textContent='Broadcast profile: '+(data.active||'none');
  const chosen=query.has('profile')?query.get('profile'):(data.active||profiles[0]?.name);
  if(!profiles.some(profile=>profile.name===chosen)){workspaceStatus('Select an existing analysis profile.');return;}
  $('workspace-profile').value=chosen;await workspaceProfile(chosen);
  if(query.has('rec'))await tmOpenReference({profile:chosen,rec:query.get('rec'),session:Number(query.get('session')),lap:Number(query.get('lap')),recording_id:query.get('recording_id'),location_m:query.has('location_m')?Number(query.get('location_m')):null});
}

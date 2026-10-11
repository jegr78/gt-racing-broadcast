/* Optional post-session analysis. Provider text is always displayed as text. */
(() => {
'use strict';
const state={settings:null,profile:null,generation:0,preview:null,selection:null,job:null,lastJob:null};
const $id=id=>document.getElementById(id);
const node=(tag,text,parent)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(parent)parent.append(e);return e;};
const error=doc=>doc?.error?.message||'Analysis operation failed';
async function request(operation,payload=null){
 const url='/api/ai/'+operation,scoped=!['settings','probe'].includes(operation),profile=tmState.profile;
 if(scoped&&!profile)return {ok:false,error:{code:'invalid_profile',message:'Select a profile before using analysis.'}};
 if(scoped){if(payload&&!payload.query)payload={...payload,profile};else payload={query:{...payload?.query,profile}};}
 try {const response=await fetch(url+(payload?.query?'?'+new URLSearchParams(payload.query):''),payload&&!payload.query?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});const doc=await response.json();if(scoped&&(profile!==tmState.profile||(doc.profile&&doc.profile!==profile)))return {ok:false,error:{code:'profile_changed',message:'Active profile changed. Reload the selection explicitly.'}};return doc;}
 catch(_e){return {ok:false,error:{message:'Control Center not reachable. Your telemetry remains available.'}};}
}
function say(id,text){$id(id).textContent=text||'';}
function option(select,value,label){const o=new Option(label,value);select.append(o);}
function invalidate(){state.generation++;state.preview=null;$id('ai-start').disabled=true;$id('ai-export-preview').disabled=true;say('ai-preview','Selection changed. Prepare and inspect a new preview.');}
function personal(){return {instructions:$id('ai-instructions').checked,skills:$id('ai-skills').checked,mcp:$id('ai-mcp').checked};}
function populateConfig(config={}){
 $id('ai-name').value=config.name||'';$id('ai-provider').value=config.provider||'codex';$id('ai-executable').value=config.executable||'';
 $id('ai-config-model').value=config.model||'';$id('ai-timeout').value=config.timeout||600;$id('ai-mode').value=config.mode||'isolated';
 ['instructions','skills','mcp'].forEach(k=>$id('ai-'+k).checked=!!config.personal?.[k]);say('ai-probe','');
}
async function settings(){
 const doc=await request('settings');if(!doc.ok){say('ai-settings-status',error(doc));return;}
 state.settings=doc;$id('ai-enabled').checked=doc.enabled;
 const select=$id('ai-configs'),selected=select.value;select.textContent='';option(select,'','New configuration');
 doc.agents.forEach(a=>option(select,a.id,a.name+' · '+a.provider));select.value=selected&&doc.agents.some(a=>a.id===selected)?selected:'';
 populateConfig(doc.agents.find(a=>a.id===select.value));refreshAgents();
}
function refreshAgents(){
 const select=$id('ai-agent'),previous=select.value,model=$id('ai-model').value,chosen=select.value||state.settings?.last?.agent;select.textContent='';
 (state.settings?.agents||[]).forEach(a=>option(select,a.id,a.name+' · '+a.provider));
 if(chosen&&state.settings.agents.some(a=>a.id===chosen))select.value=chosen;
 const agent=state.settings?.agents.find(a=>a.id===select.value);$id('ai-model').value=previous===select.value&&model?model:(state.settings?.last?.agent===select.value?state.settings.last.model:null)||agent?.model||'';
 $id('ai-load-selection').disabled=!state.settings?.enabled;
 if(!previous&&state.settings?.last?.template)$id('ai-template').value=state.settings.last.template;
 invalidate();
 $id('ai-controls').hidden=!state.settings?.enabled;
 say('ai-disabled',state.settings?.enabled?'':'Optional AI analysis is disabled. Enable it in General Settings. Existing telemetry and saved analyses remain available.');
}
async function saveSettings(remove=false){
 if(!state.settings)return;
 const selected=$id('ai-configs').value,doc=structuredClone(state.settings);delete doc.ok;let savedId=selected;
 if(remove){if(!selected)return;doc.agents=doc.agents.filter(a=>a.id!==selected);if(doc.last?.agent===selected)doc.last={};}
 else if($id('ai-name').value.trim()){
  const id=selected||'agent-'+crypto.randomUUID();
  const configuration={id,name:$id('ai-name').value.trim(),provider:$id('ai-provider').value,executable:$id('ai-executable').value.trim(),model:$id('ai-config-model').value.trim(),timeout:Number($id('ai-timeout').value),mode:$id('ai-mode').value,personal:personal()};
  doc.agents=doc.agents.filter(a=>a.id!==id);doc.agents.push(configuration);savedId=id;
 }
 doc.enabled=$id('ai-enabled').checked;
 const result=await request('settings',doc);say('ai-settings-status',result.ok?'Analysis settings saved.':error(result));
 if(result.ok){state.settings=result;await settings();if(savedId&&result.agents.some(a=>a.id===savedId)){$id('ai-configs').value=savedId;populateConfig(result.agents.find(a=>a.id===savedId));}invalidate();}
}
async function probe(){
 const id=$id('ai-configs').value;if(!id){say('ai-probe','Save a named configuration before checking its CLI.');return;}
 say('ai-probe','Checking installed CLI and existing subscription login…');
 const result=await request('probe',{query:{id}});
 say('ai-probe',result.ok?result.status+' · '+(result.version||'version unavailable')+(result.guidance?' · '+result.guidance:''):error(result));
 const list=$id('ai-model-suggestions');list.textContent='';(result.model_suggestions||[]).forEach(m=>option(list,m,m));
}
async function selection(){
 if(!state.settings)await settings();
 if(!state.settings?.enabled||!tmState.rec)return;
 state.profile=tmState.profile;invalidate();$id('ai-preview-button').disabled=true;const generation=state.generation;
 say('ai-selection-status','Loading completed sessions and explicit reference suggestions…');
 const result=await request('selection',{query:{rec:tmState.rec}});
 if(generation!==state.generation||(result.ok&&result.profile!==tmState.profile))return;
 if(!result.ok){say('ai-selection-status',error(result));return;}
 state.selection=result;$id('ai-preview-button').disabled=!result.sessions.length;const select=$id('ai-session');select.textContent='';result.sessions.forEach(s=>option(select,s.session,'Session '+s.session));
 say('ai-selection-status',result.sessions.length?'':'No completed GT7 session is available. Finish the session; stopping the recorder alone does not finish an unlimited practice session.');renderLaps();await history();
}
async function renderLaps(){
 if(!state.selection)return;
 const session=state.selection.sessions.find(s=>String(s.session)===$id('ai-session').value),box=$id('ai-laps');box.textContent='';invalidate();
 (session?.laps||[]).forEach(l=>{const label=node('label',null,box),input=node('input',null,label);input.type='checkbox';input.value=l.lap;input.checked=l.usable;input.onchange=invalidate;node('span',' Lap '+l.lap+(l.usable?'':' · excluded: '+l.reasons.join(', ')),label);});
 const generation=state.generation,chosen=$id('ai-session').value;const references=$id('ai-references');references.textContent='';
 if(!session)return;
 const result=await request('references',{query:{rec:state.selection.rec,session:chosen}});
 if(generation!==state.generation||chosen!==$id('ai-session').value)return;
 if(!result.ok){node('p',error(result),references);return;}
 result.suggestions.forEach(ref=>{const label=node('label',null,references),input=node('input',null,label);input.type='checkbox';input.dataset.reference=JSON.stringify(ref);input.onchange=invalidate;node('span',' '+ref.rec+' · S'+ref.session+' · lap '+ref.lap+' · '+ref.kind,label);});
 if(!result.suggestions.length)node('p','No compatible reference suggestions. Your selected session can still be analysed.',references);
}
function payload(){return {profile:state.profile,rec:state.selection?.rec,session:Number($id('ai-session').value),laps:[...$id('ai-laps').querySelectorAll('input:checked')].map(e=>Number(e.value)),references:[...$id('ai-references').querySelectorAll('input:checked')].map(e=>{const r=JSON.parse(e.dataset.reference);return {rec:r.rec,session:r.session,lap:r.lap};}),agent:$id('ai-agent').value,model:$id('ai-model').value,template:$id('ai-template').value,goal:$id('ai-goal').value,questions:$id('ai-questions').value,language:$id('ai-language').value,ui_language:document.documentElement.lang||'en'};}
async function preview(){
 invalidate();const generation=state.generation;say('ai-preview','Preparing the selected data…');
 const selected=payload(),result=await request('preview',selected);if(generation!==state.generation)return;
 const box=$id('ai-preview');box.textContent='';if(!result.ok){node('p',error(result),box);return;}
 state.preview={result,payload:selected};const m=result.manifest;
 node('p',m.transmission,box);node('p',m.laps.length+' selected usable laps · '+m.references.length+' explicitly selected references · report language '+m.language,box);
 node('p',Math.ceil(m.detail_bytes/1024)+' KiB detailed telemetry · '+Math.ceil(m.summary_bytes/1024)+' KiB summary · one provider invocation',box);
 node('h4','Included context notes',box);if(!m.notes.length)node('p','No included notes.',box);
 m.notes.forEach(n=>node('p',typeof n==='string'?n:[n.scope==='recording'?'Recording':'Session '+n.session,n.lap!=null?'lap '+n.lap:'',n.text].filter(Boolean).join(' · '),box));
 node('h4','Selected comparison context',box);
 (m.lap_contexts||[]).forEach(c=>{node('p',c.rec+' · S'+c.session+' lap '+c.lap+' · '+c.car+' · '+c.layout+' · '+c.compound+' · '+c.role+' · '+(c.confirmed?'context confirmed':'context unconfirmed'),box);
  const fields=Object.entries(c.settings||{});if(fields.length)node('p','Supplied conditions: '+fields.map(([k,v])=>k.replaceAll('_',' ')+' = '+String(v)).join(', '),box);const objective=Object.entries(c.objective||{});if(objective.length)node('p','Supplied driving objective: '+objective.map(([k,v])=>k.replaceAll('_',' ')+' = '+String(v)).join(', '),box);});
 node('h4','Limitations and comparability',box);m.limitations.forEach(v=>node('p',v,box));
 node('p',result.availability.guidance||'CLI ready. Subscription quota and model entitlement are not guaranteed.',box);
 if(result.agent?.mode==='personal')node('p','Personal instructions/skills may supply additional content through the CLI. Review that configuration externally; the telemetry preview does not enumerate its contents.',box);
 $id('ai-start').disabled=!result.can_start;$id('ai-export-preview').disabled=false;
}
async function start(){
 if(!state.preview)return;const selected={...state.preview.payload,confirm_preview:state.preview.result.confirm_preview};$id('ai-start').disabled=true;
 const result=await request('start',selected);say('ai-run-status',result.ok?'Analysis queued.':error(result));
 if(result.ok){state.job=result.id;state.lastJob=result.id;state.preview=null;await status();}
}
function download(result){
 if(!result.ok){say('ai-run-status',error(result));return;}
 const bytes=Uint8Array.from(atob(result.content),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type:result.mime}));
 const a=node('a');a.href=url;a.download=result.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function exportPreview(){if(state.preview)download(await request('package-export',{...state.preview.payload,confirm_preview:state.preview.result.confirm_preview}));}
async function exportRun(id,format){download(await request('export',{query:{id,format}}));}
async function status(){
 const generation=state.generation,result=await request('status');if(generation!==state.generation||!result.ok)return;
 const active=result.active;$id('ai-cancel').disabled=!result.busy;
 if(active){state.job=active.id;state.lastJob=active.id;say('ai-run-status',active.foreign?'An analysis is running in another profile. Its data remain hidden.':active.state+' · '+active.progress);}
 else if(state.job){const id=state.job;await openRun(id);if(generation===state.generation&&state.job===id)state.job=null;await history();}
}
async function cancel(){if(!state.job)return;const result=await request('cancel',{id:state.job});say('ai-run-status',result.ok?'Cancellation requested. Already consumed subscription quota cannot be recovered.':error(result));}
async function history(){
 const generation=state.generation,result=await request('history',{query:state.selection?.rec?{rec:state.selection.rec}:{}});
 if(generation!==state.generation)return;const box=$id('ai-history');box.textContent='';if(!result.ok){node('p',error(result),box);return;}
 result.runs.forEach(run=>{const row=node('div',null,box),button=node('button',new Date(run.created_at).toLocaleString()+' · '+run.state+(run.stale?' · stale':'')+' · '+(run.requested_model||'model unavailable'),row);button.onclick=()=>openRun(run.id);});
 if(!result.runs.length)node('p','No saved analyses in this profile/recording.',box);
}
async function openRun(id){
 const generation=state.generation,result=await request('job',{query:{id}});if(generation!==state.generation)return;
 const box=$id('ai-report');box.textContent='';if(!result.ok){node('p',error(result),box);return;}
 say('ai-run-status',result.state+' · '+(result.error?.message||result.progress||''));
 node('h3','Analysis · '+result.state+(result.stale?' · stale':''),box);
 if(result.stale)node('p',result.reason,box);
 node('p','Requested model: '+result.requested_model+' · reported model: '+(result.actual_model||'not reported'),box);
 const exports=node('div',null,box);for(const format of result.state==='completed'?['html','markdown','package']:['package']){const b=node('button',format==='package'?'Export input package':'Export '+format,exports);b.onclick=()=>exportRun(id,format);}
 if(result.report){
  node('p','Report language: '+result.report.language+'. Supplied facts were checked against the package and retain their source provenance; interpretations and possible causes remain unproven.',box);
  for(const [label,items] of [['Findings',result.report.findings],['Three practice exercises',result.report.exercises]]){
   node('h3',label,box);items.forEach(item=>{const article=node('article',null,box);node('h4','Priority '+item.priority+' · '+(item.title||item.action),article);
    if(item.interpretation)node('p',item.interpretation,article);if(item.check)node('p','Check: '+item.check,article);
    if(item.possible_causes?.length)node('p','Possible causes: '+item.possible_causes.join('; '),article);
    node('h5','Validated package facts',article);item.evidence.forEach(e=>node('p',(e.label||e.fact_id)+': '+e.value+' · '+e.provenance,article));
    const link=node('button','Open lap'+(item.location_m!=null?' at '+item.location_m+' m':''),article);link.onclick=()=>tmOpenReference({...item.telemetry,profile:result.profile});
    (item.comparisons||[]).forEach(ref=>{const compare=node('button','Compare with S'+ref.session+' lap '+ref.lap,article);compare.onclick=()=>openComparison(item,result.profile,ref);});
   });
  }
  node('h3','Limitations',box);result.report.limitations.forEach(v=>node('p',v,box));
 }
 const details=node('details',null,box);node('summary','Run provenance',details);const provenance={...result};delete provenance.report;delete provenance.ok;node('pre',JSON.stringify(provenance,null,2),details);
 const retry=node('button','Prepare a new run',box);retry.onclick=()=>{invalidate();$id('ai-controls').scrollIntoView({block:'start'});};
}
async function openComparison(item,profile,reference){
 if(!await tmOpenReference({...item.telemetry,profile}))return;
 const primary=tmState.b,stem=v=>String(v).replace(/\.gt7rec$/,'');
 const target=(tmState.pool?.laps||[]).find(l=>stem(l.rec)===stem(reference.rec)&&l.session===reference.session&&l.lap===reference.lap);
 if(!target){tmErr('The saved comparison reference is unavailable in the current telemetry pool.');return;}
 const key=tmKey(target),data=await tmFetchLap(key);
 if(profile!==tmState.profile||primary!==tmState.b)return;
 if(data.lap?.recording_id!==reference.recording_id){tmErr('Comparison recording identity changed. The historical report remains a snapshot.');return;}
 tmState.a=key;tmFillPickers(tmState.lapB);tmRenderLaps();await tmLoadPair();
 if(item.location_m!=null){const chart=tmState.chart;if(chart){const rect=$id('tm-charts').getBoundingClientRect();tmHover({clientX:rect.left+chart.X(Math.min(item.location_m,chart.maxD))/chart.W*rect.width});} }
}
function reset(profile){state.profile=profile;state.generation++;state.selection=null;state.preview=null;state.job=null;state.lastJob=null;['ai-report','ai-history','ai-preview','ai-laps','ai-references','ai-selection-status','ai-run-status'].forEach(id=>say(id,''));$id('ai-start').disabled=true;$id('ai-export-preview').disabled=true;$id('ai-preview-button').disabled=true;if(profile&&currentView==='telemetry'){status();history();}}
function init(){
 const settingsCard=node('section',null,document.querySelector('[data-view="settings"]'));settingsCard.id='ai-settings';settingsCard.className='card rcai';
 settingsCard.innerHTML=`<h3>Optional AI telemetry analysis</h3><p>Uses your existing Codex or Claude Code subscription CLI. Install and sign in externally. Racecast never installs an agent or selects API billing.</p>
 <label><input id="ai-enabled" type="checkbox"> Enable analysis on this machine</label>
 <div class="rcai-grid"><label>Named configurations<select id="ai-configs"></select></label><label>Name<input id="ai-name"></label><label>Provider<select id="ai-provider"><option value="codex">Codex</option><option value="claude">Claude Code</option></select></label><label>Executable (optional)<input id="ai-executable" placeholder="CLI command or full executable path"></label><label>Default model<input id="ai-config-model" list="ai-model-suggestions"></label><label>Timeout (seconds)<input id="ai-timeout" type="number" min="10" max="3600" value="600"></label><label>Agent mode<select id="ai-mode"><option value="isolated">Isolated</option><option value="personal">Personal</option></select></label></div>
 <p>Personal content must be chosen explicitly. MCP servers cannot be confined to the analysis package; use a manual run externally when needed. Selective personal Claude customizations are currently unavailable for background jobs.</p>
 <label><input id="ai-instructions" type="checkbox"> Include personal instructions</label> <label><input id="ai-skills" type="checkbox"> Include personal skills</label> <label><input id="ai-mcp" type="checkbox"> Request personal MCP</label>
 <div class="row"><button id="ai-save">Save analysis settings</button><button id="ai-remove">Remove configuration</button><button id="ai-check">Check CLI / login</button></div><p id="ai-settings-status" role="status"></p><p id="ai-probe" role="status"></p><datalist id="ai-model-suggestions"></datalist>`;
 const card=node('section',null,document.querySelector('[data-view="telemetry"]'));card.id='ai-analysis';card.className='card rcai';
 card.innerHTML=`<h3>Post-session AI analysis</h3><p id="ai-disabled"></p><div class="row"><button id="ai-load-selection">Prepare analysis selection</button><button id="ai-history-refresh">Refresh history</button></div><div id="ai-controls" hidden>
 <p id="ai-selection-status" role="status"></p><div class="rcai-grid"><label>Completed GT7 session<select id="ai-session"></select></label><label>Template<select id="ai-template"><option value="session-overview">Session overview</option><option value="driving-technique">Driving technique</option><option value="consistency">Consistency</option></select></label><label>Agent<select id="ai-agent"></select></label><label>Model<input id="ai-model" list="ai-model-suggestions"></label><label>Report language<input id="ai-language" value="en" maxlength="40"></label></div>
 <h4>Completed laps</h4><div id="ai-laps" class="rcai-options"></div><h4>Optional compatible references · none selected automatically</h4><div id="ai-references" class="rcai-options"></div><div class="rcai-grid"><label>Practice goal<textarea id="ai-goal" maxlength="4000"></textarea></label><label>Additional questions<textarea id="ai-questions" maxlength="4000"></textarea></label></div>
 <div class="row"><button id="ai-preview-button" disabled>Preview selected data</button><button id="ai-export-preview" disabled>Export preview package</button><button id="ai-start" disabled>Start this analysis</button></div><div id="ai-preview"></div></div>
 <p>One analysis can run on this machine. Subscription quota/model availability are not guaranteed. Cancellation cannot recover already consumed quota.</p><div class="row"><button id="ai-cancel" disabled>Cancel active analysis</button></div><p id="ai-run-status" role="status"></p><h4>Saved analysis history</h4><div id="ai-history"></div><div id="ai-report"></div>`;
 const style=node('style',`.rcai{overflow-wrap:anywhere}.rcai-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:14px 0}.rcai-grid label{display:flex;flex-direction:column;gap:6px;min-width:0}.rcai input:not([type=checkbox]),.rcai select,.rcai textarea{width:100%;min-width:0;padding:8px;background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:6px;font:inherit}.rcai textarea{min-height:80px}.rcai-options{display:flex;flex-wrap:wrap;gap:12px}.rcai pre{white-space:pre-wrap;font-size:12px}.rcai article{border-top:1px solid var(--line);padding-top:10px}.rcai button{margin:4px}`);document.head.append(style);
  $id('ai-mode').onchange=()=>{if($id('ai-mode').value==='isolated')['instructions','skills','mcp'].forEach(k=>$id('ai-'+k).checked=false);};
 $id('ai-configs').onchange=()=>populateConfig(state.settings?.agents.find(a=>a.id===$id('ai-configs').value));
 $id('ai-save').onclick=()=>saveSettings();$id('ai-remove').onclick=()=>saveSettings(true);$id('ai-check').onclick=probe;
 $id('ai-load-selection').onclick=selection;$id('ai-session').onchange=renderLaps;$id('ai-preview-button').onclick=preview;$id('ai-start').onclick=start;$id('ai-export-preview').onclick=exportPreview;$id('ai-cancel').onclick=cancel;$id('ai-history-refresh').onclick=history;
 ['ai-model','ai-template','ai-goal','ai-questions','ai-language'].forEach(id=>$id(id).oninput=invalidate);
 $id('ai-agent').onchange=()=>{const a=state.settings?.agents.find(a=>a.id===$id('ai-agent').value);$id('ai-model').value=a?.model||'';invalidate();};
 window.RacecastAI={settings,selection,reset,status,history};settings().then(()=>{if(tmState.profile&&currentView==='telemetry'){status();history();}});
 setInterval(()=>{if(currentView==='telemetry')status();},1500);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();

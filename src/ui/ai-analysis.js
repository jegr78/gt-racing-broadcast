/* Optional post-session analysis. Provider text is always displayed as text. */
(() => {
'use strict';
const state={settings:null,settingsDirty:false,settingsBusy:false,models:{},profile:null,generation:0,preview:null,selection:null,job:null,lastJob:null,previewRequest:null,startBusy:false,machineBusy:false,cancelJob:null,run:null};
const $id=id=>document.getElementById(id);
const node=(tag,text,parent)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(parent)parent.append(e);return e;};
const error=doc=>doc?.error?.message||'Analysis operation failed';
const FINISHED=new Set(['completed','awaiting_validation','failed','cancelled','timed_out']);
async function request(operation,payload=null){
 const url='/api/ai/'+operation,scoped=!['settings','probe'].includes(operation),profile=tmState.profile;
 if(scoped&&!profile)return {ok:false,error:{code:'invalid_profile',message:'Select a profile before using analysis.'}};
 if(scoped){if(payload&&!payload.query)payload={...payload,profile};else payload={query:{...payload?.query,profile}};}
 try {const response=await fetch(url+(payload?.query?'?'+new URLSearchParams(payload.query):''),payload&&!payload.query?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{});const doc=await response.json();if(scoped&&(profile!==tmState.profile||(doc.profile&&doc.profile!==profile)))return {ok:false,error:{code:'profile_changed',message:'Active profile changed. Reload the selection explicitly.'}};return doc;}
 catch(_e){return {ok:false,error:{message:'Local UI service not reachable. Your telemetry remains available.'}};}
}
function say(id,text){$id(id).textContent=text||'';}
function option(select,value,label){const o=new Option(label,value);select.append(o);}
function feedback(kind,title,message,run=null){
 const box=$id('ai-feedback');box.dataset.kind=kind;box.hidden=false;
 say('ai-feedback-title',title);say('ai-run-status',message);
 const busy=['queued','starting','running','validating','cancelling','preparing'].includes(kind);
 $id('ai-progress').hidden=!busy;$id('ai-new-run').hidden=!FINISHED.has(kind)&&kind!=='interrupted';
 $id('ai-view-report').hidden=!FINISHED.has(kind)&&kind!=='interrupted';$id('ai-view-report').textContent=kind==='completed'?'View report':'View details';
 if(run?.report?.language==='de'){$id('ai-view-report').textContent='Bericht ansehen';$id('ai-new-run').textContent='Neue Analyse vorbereiten';}
 else $id('ai-new-run').textContent='Prepare a new run';
 if(run){state.run=run;const start=Date.parse(run.started_at||run.created_at),end=Date.parse(run.finished_at)||Date.now();
  const seconds=Number.isFinite(start)?Math.max(0,Math.floor((end-start)/1000)):null;
  say('ai-run-meta',[run.agent?.name||run.agent?.provider,run.requested_model,seconds!=null?Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')+(run.report?.language==='de'?' Laufzeit':' elapsed'):''].filter(Boolean).join(' · '));
 }else say('ai-run-meta','');
}
function runFeedback(run){
 const titles={queued:'Analysis queued',starting:'Checking provider',running:'Analysing selected laps',validating:'Checking report facts',completed:'Report ready',failed:'Analysis failed',timed_out:'Analysis timed out',cancelled:'Analysis cancelled',interrupted:'Analysis interrupted',awaiting_validation:'Report awaiting validation'};
 const help={validation_failed:'The provider returned a report that failed the evidence checks. No verified report was saved. Choose View details, then open Technical details for the cause.',provider_failed:'The provider could not finish the analysis. Choose View details, then open Technical details before preparing another run.',timed_out:'The configured time limit was reached. You can adjust the timeout in Data management before preparing another run.',cancelled:'The analysis stopped. Its input package is retained.',interrupted:'The process ended without a verified report. Its input package is retained.'};
 const code=run.error?.code||run.state;
 const phases={running:'The provider is analysing your selected telemetry. This may take several minutes. You can leave this tab open or return later.',validating:'Matching report evidence to the frozen telemetry package before saving the result.',completed:'The report passed the evidence checks. '+(run.report?.findings.length||0)+' finding'+(run.report?.findings.length===1?'':'s')+' and '+(run.report?.exercises.length||0)+' practice exercises are ready. Choose View report to read them.'};
 if(run.report?.language==='de'&&run.state==='completed'){titles.completed='Bericht bereit';phases.completed='Die zitierten Messwerte wurden geprüft. '+run.report.findings.length+' Fahrhinweise und '+run.report.exercises.length+' Übungen sind verfügbar.';}
 if(state.cancelJob===run.id&&!FINISHED.has(run.state))feedback('cancelling','Stopping analysis','Waiting for the provider process to exit. Already consumed subscription quota cannot be recovered.',run);
 else feedback(run.state,titles[run.state]||run.state,help[code]||phases[run.state]||run.error?.message||run.progress||'',run);
}
function disclosure(parent,title){const box=node('details',null,parent);node('summary',title,box);return box;}
function ensureSelection(){const rec=tmState.lapB?.rec||tmState.rec;if(rec&&(!state.selection||state.selection.rec!==rec))selection();}
function prepareNewRun(){if(typeof workspaceAI==='function')workspaceAI();invalidate();selection();$id('ai-controls').scrollIntoView({block:'start',behavior:'smooth'});}
function invalidate(){state.generation++;state.preview=null;$id('ai-start').disabled=true;$id('ai-export-preview').disabled=true;say('ai-preview','Selection changed. Prepare and inspect a new preview.');}
function personal(){return {instructions:$id('ai-instructions').checked,skills:$id('ai-skills').checked,mcp:$id('ai-mcp').checked};}
function modelSuggestions(){
 for(const [listId,inputId,agentId,provider] of [
  ['ai-model-suggestions','ai-config-model',$id('ai-configs').value,$id('ai-provider').value],
  ['ai-run-model-suggestions','ai-model',$id('ai-agent').value,state.settings?.agents.find(a=>a.id===$id('ai-agent').value)?.provider]
 ]){
  const list=$id(listId),cached=state.models[agentId];list.textContent='';
  const models=provider==='claude'?['sonnet','opus','haiku']:cached&&cached.provider===provider?cached.models:[];
  models.forEach(m=>option(list,m,provider==='claude'?m+' (Claude Code alias)':m));
  $id(inputId).placeholder=provider==='claude'?'sonnet, opus, haiku or a full model ID':'Exact model name for this provider';
  $id(inputId).title=provider==='claude'?'Claude Code aliases follow the CLI version and account settings. Enter a full model ID to pin a version.':'';
 }
}
function settingsStatus(text,kind='info'){
 const status=$id('ai-settings-status');status.textContent=text;status.dataset.kind=kind;
}
function settingsControls(){
 const selected=!!$id('ai-configs').value;
 $id('ai-save').disabled=!state.settings||state.settingsBusy;
 $id('ai-save-enabled').disabled=!state.settings||state.settingsBusy||$id('ai-enabled').checked===state.settings.enabled;
 $id('ai-save').textContent=state.settingsBusy?'Saving…':selected?'Save configuration':'Save new configuration';
 $id('ai-remove').disabled=!selected||state.settingsBusy;
 $id('ai-check').disabled=!selected||state.settingsDirty||state.settingsBusy;
 const supported=$id('ai-mode').value==='personal'&&$id('ai-provider').value==='codex';
 ['instructions','skills','mcp'].forEach(k=>{
  const input=$id('ai-'+k);input.disabled=state.settingsBusy||!supported||k==='mcp';
  if(!supported||k==='mcp')input.checked=false;
 });
 say('ai-mode-help',$id('ai-mode').value==='isolated'?'Isolated mode uses no personal instructions or skills. Choose Personal to include them.':supported?'Choose which personal instructions and skills to include.':'Personal Claude instructions and skills are unavailable for background analysis.');
}
function settingsChanged(){
 state.settingsDirty=true;settingsControls();settingsStatus('Unsaved changes. Save before checking the CLI.','pending');say('ai-probe','');
}
function populateConfig(config={}){
 $id('ai-name').value=config.name||'';$id('ai-provider').value=config.provider||'codex';$id('ai-executable').value=config.executable||'';
 $id('ai-config-model').value=config.model||'';$id('ai-timeout').value=config.timeout||600;$id('ai-mode').value=config.mode||'isolated';
 ['instructions','skills','mcp'].forEach(k=>$id('ai-'+k).checked=!!config.personal?.[k]);say('ai-probe','');settingsControls();modelSuggestions();
}
async function settings(){
 const doc=await request('settings');if(!doc.ok){settingsStatus(error(doc),'error');return;}
 renderSettings(doc,$id('ai-configs').value);
 settingsStatus($id('ai-configs').value?'Saved configuration loaded.':'Enter a name, choose a provider, then save the configuration.');
}
function renderSettings(doc,selected=''){
 const previousProvider=state.settings?.agents.find(a=>a.id===$id('ai-agent').value)?.provider;
 state.settingsDirty=false;
 state.settings=doc;$id('ai-enabled').checked=doc.enabled;
 const select=$id('ai-configs');select.textContent='';option(select,'','New configuration');
 doc.agents.forEach(a=>option(select,a.id,a.name+' · '+a.provider));select.value=selected&&doc.agents.some(a=>a.id===selected)?selected:'';
 populateConfig(doc.agents.find(a=>a.id===select.value));refreshAgents(previousProvider===doc.agents.find(a=>a.id===$id('ai-agent').value)?.provider);
}
function refreshAgents(preserveModel=true){
 const select=$id('ai-agent'),previous=select.value,model=$id('ai-model').value,chosen=select.value||state.settings?.last?.agent;select.textContent='';
 (state.settings?.agents||[]).forEach(a=>option(select,a.id,a.name+' · '+a.provider));
 if(chosen&&state.settings.agents.some(a=>a.id===chosen))select.value=chosen;
 const agent=state.settings?.agents.find(a=>a.id===select.value);$id('ai-model').value=preserveModel&&previous===select.value&&model?model:(state.settings?.last?.agent===select.value?state.settings.last.model:null)||agent?.model||'';
 modelSuggestions();
 $id('ai-load-selection').disabled=!state.settings?.enabled;
 if(!previous&&state.settings?.last?.template)$id('ai-template').value=state.settings.last.template;
 invalidate();
 $id('ai-controls').hidden=!state.settings?.enabled;
 say('ai-disabled',state.settings?.enabled?'':'Optional AI analysis is disabled. Enable it in Data management. Existing telemetry and saved analyses remain available.');
}
async function saveSettings(remove=false,enableOnly=false){
 if(!state.settings||state.settingsBusy)return;
 const selected=$id('ai-configs').value,doc=structuredClone(state.settings);delete doc.ok;let savedId=selected;
 const name=$id('ai-name').value.trim();
 if(!remove&&!enableOnly&&!name){settingsStatus('Enter a configuration name before saving.','error');$id('ai-name').focus();return;}
 if(!remove&&!enableOnly&&!$id('ai-timeout').checkValidity()){settingsStatus('Timeout must be a whole number from 10 to 3600 seconds.','error');$id('ai-timeout').focus();return;}
 if(remove){if(!selected)return;doc.agents=doc.agents.filter(a=>a.id!==selected);if(doc.last?.agent===selected)doc.last={};}
 else if(!enableOnly){
  const id=selected||'agent-'+crypto.randomUUID();
  const configuration={id,name:$id('ai-name').value.trim(),provider:$id('ai-provider').value,executable:$id('ai-executable').value.trim(),model:$id('ai-config-model').value.trim(),timeout:Number($id('ai-timeout').value),mode:$id('ai-mode').value,personal:personal()};
  if(doc.last?.agent===id&&doc.agents.find(a=>a.id===id)?.provider!==configuration.provider)delete doc.last.model;
  doc.agents=doc.agents.filter(a=>a.id!==id);doc.agents.push(configuration);savedId=id;
 }
 doc.enabled=$id('ai-enabled').checked;
 state.settingsBusy=true;
 const fields=[...$id('ai-settings').querySelectorAll('input,select')];fields.forEach(e=>e.disabled=true);settingsControls();settingsStatus('Saving analysis settings…','pending');
 const result=await request('settings',doc);
 state.settingsBusy=false;fields.forEach(e=>e.disabled=false);
 if(result.ok){
  if(enableOnly){state.settings=result;refreshAgents();settingsControls();settingsStatus('Analysis is '+(result.enabled?'enabled':'disabled')+' on this machine.'+(state.settingsDirty?' Configuration edits are still unsaved.':''),'success');}
  else {renderSettings(result,remove?'':savedId);settingsStatus(remove?'Configuration removed.':'Configuration "'+name+'" saved. Analysis is '+(result.enabled?'enabled':'disabled')+' on this machine. You can now check the CLI / login.','success');}
 }
 else {settingsControls();settingsStatus('Could not save: '+error(result),'error');}
}
async function probe(){
 const id=$id('ai-configs').value;if(!id){say('ai-probe','Save a named configuration before checking its CLI.');return;}
 if(state.settingsDirty||state.settingsBusy)return;
 const provider=$id('ai-provider').value;
 say('ai-probe','Checking installed CLI and existing subscription login…');
 const result=await request('probe',{query:{id}});
 if(result.ok)state.models[id]={provider,models:result.model_suggestions||[]};
 modelSuggestions();
 if(id!==$id('ai-configs').value||provider!==$id('ai-provider').value||state.settingsDirty)return;
 say('ai-probe',result.ok?result.status+' · '+(result.version||'version unavailable')+(result.guidance?' · '+result.guidance:''):error(result));
}
async function selection(){
 if(!state.settings)await settings();
 const rec=tmState.lapB?.rec||tmState.rec;
 if(!state.settings?.enabled||!rec)return;
 state.profile=tmState.profile;invalidate();$id('ai-preview-button').disabled=true;const generation=state.generation;
 say('ai-selection-status','Loading completed sessions and explicit reference suggestions…');
 const result=await request('selection',{query:{rec}});
 if(generation!==state.generation||(result.ok&&result.profile!==tmState.profile))return;
 if(!result.ok){say('ai-selection-status',error(result));return;}
 state.selection=result;$id('ai-preview-button').disabled=!result.sessions.length;const select=$id('ai-session');select.textContent='';result.sessions.forEach(s=>option(select,s.session,'Session '+s.session));
 say('ai-selection-status',result.sessions.length?'':'No completed GT7 session is available. Finish the session; stopping the recorder alone does not finish an unlimited practice session.');renderLaps();await history();
}
async function renderLaps(){
 if(!state.selection)return;
 const session=state.selection.sessions.find(s=>String(s.session)===$id('ai-session').value),box=$id('ai-laps');box.textContent='';invalidate();
  (session?.laps||[]).forEach(l=>{const label=node('label',null,box),input=node('input',null,label);label.className=l.usable?'rcai-lap':'rcai-lap rcai-excluded';input.type='checkbox';input.value=l.lap;input.checked=l.usable;input.disabled=!l.usable;input.onchange=invalidate;node('span','Lap '+l.lap,label);if(!l.usable){label.title=l.reasons.join(', ');node('small','Excluded',label);}});
 say('ai-lap-count',(session?.laps||[]).filter(l=>l.usable).length+' usable of '+(session?.laps||[]).length+' captured laps. Excluded laps remain available in Telemetry.');
 const generation=state.generation,chosen=$id('ai-session').value;const references=$id('ai-references');references.textContent='';
 if(!session)return;
 const result=await request('references',{query:{rec:state.selection.rec,session:chosen}});
 if(generation!==state.generation||chosen!==$id('ai-session').value)return;
 if(!result.ok){node('p',error(result),references);return;}
 const groups=new Map();result.suggestions.forEach(ref=>{const key=ref.rec+' · Session '+ref.session;if(!groups.has(key)){const group=node('fieldset',null,references);node('legend',key,group);groups.set(key,group);}const label=node('label',null,groups.get(key)),input=node('input',null,label);input.type='checkbox';input.dataset.reference=JSON.stringify(ref);input.onchange=invalidate;label.title=ref.kind;node('span','Lap '+ref.lap,label);});
 if(!result.suggestions.length)node('p','No compatible reference suggestions. Your selected session can still be analysed.',references);
}
function payload(){return {profile:state.profile,rec:state.selection?.rec,session:Number($id('ai-session').value),laps:[...$id('ai-laps').querySelectorAll('input:checked')].map(e=>Number(e.value)),references:[...$id('ai-references').querySelectorAll('input:checked')].map(e=>{const r=JSON.parse(e.dataset.reference);return {rec:r.rec,session:r.session,lap:r.lap};}),agent:$id('ai-agent').value,model:$id('ai-model').value,template:$id('ai-template').value,goal:$id('ai-goal').value,questions:$id('ai-questions').value,language:$id('ai-language').value,ui_language:document.documentElement.lang||'en'};}
async function preview(){
 if(state.previewRequest||state.startBusy)return;
 invalidate();const generation=state.generation,owner={};state.previewRequest=owner;
 $id('ai-preview-button').disabled=true;$id('ai-preview-button').textContent='Preparing package…';$id('ai-preview').setAttribute('aria-busy','true');say('ai-preview','Reading selected laps, context and reference data…');
 if(!state.job)feedback('preparing','Preparing analysis package','Reading the selected telemetry locally. The provider has not been started.');
 const selected=payload(),result=await request('preview',selected);
 if(state.previewRequest===owner){state.previewRequest=null;$id('ai-preview-button').disabled=!state.selection?.sessions.length;$id('ai-preview-button').textContent='Preview selected data';$id('ai-preview').setAttribute('aria-busy','false');}
 if(generation!==state.generation)return;
 const box=$id('ai-preview');box.textContent='';if(!result.ok){feedback('error','Package could not be prepared',error(result));node('p','Adjust the selection and preview again. No provider was invoked.',box);return;}
 state.preview={result,payload:selected};const m=result.manifest;
 if(!state.job)feedback(result.can_start?'ready':'error',result.can_start?'Package ready for review':'Provider is not ready',result.can_start?'Review the package below, then start the analysis explicitly.':result.availability.guidance);
 const metrics=node('div',null,box);metrics.className='rcai-package-metrics';
 for(const [value,label] of [[m.laps.length,'usable laps'],[m.references.length,'reference laps'],[Math.ceil(m.detail_bytes/1024)+' KiB','detail data'],[Math.ceil(m.summary_bytes/1024)+' / '+Math.floor(m.limits.summary_bytes/1024)+' KiB','summary budget']]){const tile=node('div',null,metrics);node('strong',String(value),tile);node('span',label,tile);}
 node('p',m.transmission,box);node('p','Provider: '+result.agent.provider+' · Model: '+result.requested_model+' · Report language: '+m.language+' · One invocation',box);
 const notes=disclosure(box,'Included context notes · '+m.notes.length);if(!m.notes.length)node('p','No included notes.',notes);
 m.notes.forEach(n=>node('p',typeof n==='string'?n:[n.scope==='recording'?'Recording':'Session '+n.session,n.lap!=null?'lap '+n.lap:'',n.text].filter(Boolean).join(' · '),notes));
 const contexts=disclosure(box,'Selected comparison context · '+(m.lap_contexts||[]).length+' laps');
 (m.lap_contexts||[]).forEach(c=>{const lap=disclosure(contexts,'Lap '+c.lap+' · '+c.compound+' · '+c.role+' · '+(c.confirmed?'confirmed':'unconfirmed'));node('p',c.rec+' · S'+c.session+' · '+c.car+' · '+c.layout,lap);
  const fields=Object.entries(c.settings||{});if(fields.length)node('p','Supplied conditions: '+fields.map(([k,v])=>k.replaceAll('_',' ')+' = '+String(v)).join(', '),lap);const objective=Object.entries(c.objective||{});if(objective.length)node('p','Supplied driving objective: '+objective.map(([k,v])=>k.replaceAll('_',' ')+' = '+String(v)).join(', '),lap);});
 const limitations=disclosure(box,'Limitations and comparability · '+m.limitations.length);m.limitations.forEach(v=>node('p',v,limitations));
 if(result.availability.guidance)node('p',result.availability.guidance,box);
 if(result.agent?.mode==='personal')node('p','Personal instructions/skills may supply additional content through the CLI. Review that configuration externally; the telemetry preview does not enumerate its contents.',box);
 $id('ai-start').disabled=!result.can_start||state.machineBusy||state.startBusy;$id('ai-export-preview').disabled=false;
}
async function start(){
 if(!state.preview||state.startBusy||state.machineBusy||state.job)return;const selected={...state.preview.payload,confirm_preview:state.preview.result.confirm_preview},generation=state.generation;state.startBusy=true;$id('ai-start').disabled=true;
 feedback('queued','Starting analysis','Reserving the local analysis slot and checking the reviewed package.');$id('ai-feedback').scrollIntoView({block:'center',behavior:'smooth'});say('ai-report','');
 const result=await request('start',selected);state.startBusy=false;if(generation!==state.generation)return;
 if(result.ok){state.job=result.id;state.lastJob=result.id;state.preview=null;await status();}
 else {feedback('error','Analysis could not start',error(result));$id('ai-start').disabled=!state.preview?.result.can_start;}
}
function download(result){
 if(!result.ok){say('ai-run-status',error(result));return;}
 const bytes=Uint8Array.from(atob(result.content),c=>c.charCodeAt(0)),url=URL.createObjectURL(new Blob([bytes],{type:result.mime}));
 const a=node('a');a.href=url;a.download=result.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function exportPreview(){if(state.preview)download(await request('package-export',{...state.preview.payload,confirm_preview:state.preview.result.confirm_preview}));}
async function exportRun(id,format){download(await request('export',{query:{id,format}}));}
async function status(){
 const sequence=state.statusSequence=(state.statusSequence||0)+1;
 const generation=state.generation,result=await request('status');if(generation!==state.generation||sequence!==state.statusSequence)return;
 if(!result.ok){if(state.job)feedback('connection','Analysis status unavailable','Reconnecting to the local UI service. Wait for status recovery before starting another analysis.',state.run);return;}
 state.machineBusy=result.busy;
 // The runner keeps a finished run's terminal state in the active record until its cleanup
 // ends: show that run's own result instead of a terminal status above the previous report.
 const active=result.active,finished=active&&!active.foreign&&FINISHED.has(active.state);$id('ai-cancel').disabled=!result.busy||!!active?.foreign||state.cancelJob===active?.id;
 $id('ai-start').disabled=!!result.busy||!!state.startBusy||!!state.job||!state.preview?.result.can_start;
 if(active&&!finished){if(!active.foreign&&state.job!==active.id)say('ai-report','');state.job=active.foreign?null:active.id;state.lastJob=active.foreign?state.lastJob:active.id;if(active.foreign)feedback('running','Another profile is analysing','An analysis is running in another profile. Its data remain hidden.');else runFeedback(active);}
 else if(state.job){const id=state.job;await openRun(id,true);if(generation===state.generation&&state.job===id)state.job=null;await history();}
}
async function cancel(){
 if(!state.job)return;const id=state.job,generation=state.generation;$id('ai-cancel').disabled=true;
 const result=await request('cancel',{id});if(generation!==state.generation||state.job!==id)return;
 if(result.ok)state.cancelJob=id;
 feedback(result.ok?'cancelling':'error',result.ok?'Stopping analysis':'Cancellation failed',result.ok?'Waiting for the provider process to exit. Already consumed subscription quota cannot be recovered.':error(result),state.run);
}
async function history(){
 const generation=state.generation,result=await request('history',{query:state.selection?.rec?{rec:state.selection.rec}:{}});
 if(generation!==state.generation)return;const box=$id('ai-history');box.textContent='';if(!result.ok){node('p',error(result),box);return;}
 result.runs.forEach(run=>{const row=node('div',null,box),button=node('button',new Date(run.created_at).toLocaleString()+' · '+run.state+(run.stale?' · stale':'')+' · '+(run.requested_model||'model unavailable'),row);button.onclick=()=>openRun(run.id);});
 if(!result.runs.length)node('p','No saved analyses in this profile/recording.',box);
}
async function openRun(id,followJob=false){
 const generation=state.generation,result=await request('job',{query:{id}});if(generation!==state.generation||followJob&&state.job!==id)return;
 const box=$id('ai-report');box.textContent='';if(!result.ok){node('p',error(result),box);return;}
 if(!state.job||state.job===id)runFeedback(result);
 const de=result.report?.language==='de',t=(deText,enText)=>de?deText:enText;
 node('h3',t('Fahrerbericht','Driver review')+(result.stale?t(' · historischer Datenstand',' · historical snapshot'):''),box);
 if(result.stale)node('p',t('Die Quelldaten oder die Berechnung haben sich seit diesem Bericht geändert. Er zeigt weiterhin den gespeicherten Stand.',result.reason),box);
 if(result.state==='completed'){
  const link=node('a',t('Visuellen Bericht öffnen','Open visual report'),box);link.href='/api/ai/report?'+new URLSearchParams({id,profile:result.profile});link.target='_blank';link.rel='noopener';link.className='rcai-open-report';
  node('p',t('Der Bericht öffnet im Browser mit Rundenzeit-Verlauf, Fahrlinien und Messkurven.','Opens in your browser with lap-time trend, racing lines and telemetry traces.'),box);
 }
 if(result.report){
  if(result.report.summary)node('p',result.report.summary,box);
  node('p',t('Zitierte Messwerte sind geprüft. Erklärungen und mögliche Ursachen bleiben Hypothesen.','Cited measurements were checked. Interpretations and possible causes remain hypotheses.'),box);
  for(const [label,items] of [[t('Deine nächsten Fahrthemen','Your next driving priorities'),result.report.findings],[t('Übungen für den nächsten Stint','Practice for the next stint'),result.report.exercises]]){
   if(!items.length)continue;node('h3',label,box);items.forEach(item=>{const article=node('article',null,box);node('h4',t('Priorität ','Priority ')+item.priority+' · '+(item.title||item.action),article);
    if(item.location_label)node('p',t('Passage: ','Section: ')+item.location_label,article);
    for(const [field,label] of [['observation',t('Beobachtung','Observation')],['interpretation',t('Einordnung','Interpretation')],['action',t('Nächster Versuch','Next attempt')],['check',t('So prüfst du es','How to check')]]){if(item[field]&&(field!=='action'||item.title))node('p',label+': '+item[field],article);}
    if(item.possible_causes?.length)node('p',t('Mögliche Ursachen: ','Possible causes: ')+item.possible_causes.join('; '),article);
    const evidence=disclosure(article,t('Zitierte Messwerte','Cited measurements')+' · '+item.evidence.length);item.evidence.forEach(e=>node('p',(e.label||e.fact_id)+': '+(e.display_value??e.value),evidence));
    const link=node('button',t('Runde öffnen','Open lap')+(item.location_m!=null?t(' bei ',' at ')+Math.round(item.location_m)+' m':''),article);link.onclick=()=>tmOpenReference({...item.telemetry,profile:result.profile});
    (item.comparisons||[]).forEach(ref=>{const compare=node('button',t('Vergleich mit S','Compare with S')+ref.session+t(' Runde ',' lap ')+ref.lap,article);compare.onclick=()=>openComparison(item,result.profile,ref);});
   });
  }
  const notes=disclosure(box,t('Datenqualität und Grenzen der Auswertung','Data quality and limits of this review'));
  [...(result.report.technical_notes||[]),...result.report.limitations].forEach(v=>node('p',v,notes));
 }
 const exports=disclosure(box,t('Exporte und technische Details','Exports and technical details'));for(const format of result.state==='completed'?['html','markdown','package']:['package']){const b=node('button',format==='package'?t('Datenpaket exportieren','Export input package'):t('Herunterladen: ','Download ')+format,exports);b.onclick=()=>exportRun(id,format);}
 node('p',t('Angefragtes Modell: ','Requested model: ')+result.requested_model+t(' · gemeldetes Modell: ',' · reported model: ')+(result.actual_model||t('nicht gemeldet','not reported')),exports);
 if(result.diagnostics?.length){const diagnostics=disclosure(exports,t('Fehlerdiagnose','Technical details'));result.diagnostics.forEach(log=>{node('h4',log.name,diagnostics);node('pre',log.text,diagnostics);});}
 const details=disclosure(exports,t('Datenherkunft und technische Kennungen','Run provenance'));const provenance={...result};delete provenance.report;delete provenance.ok;delete provenance.diagnostics;node('pre',JSON.stringify(provenance,null,2),details);
 const retry=node('button',t('Neue Analyse vorbereiten','Prepare a new run'),box);retry.onclick=prepareNewRun;
}
async function openComparison(item,profile,reference){
 if(!await tmOpenReference({...item.telemetry,profile}))return;
 const primary=tmState.b,primaryLap=tmState.lapB,generation=tmSeq.b,stem=v=>String(v).replace(/\.gt7rec$/,'');
 const recording=tmState.recs.find(r=>stem(r.rec)===stem(reference.rec)&&!r.recording);
 if(!recording){tmErr('The saved comparison recording is unavailable.');return;}
 const key=tmKey({rec:recording.rec,session:reference.session,lap:reference.lap}),data=await tmFetchLap(key);
 if(profile!==tmState.profile||primary!==tmState.b||generation!==tmSeq.b)return;
 if(data.lap?.recording_id!==reference.recording_id){tmErr('Comparison recording identity changed. The historical report remains a snapshot.');return;}
 if(!tmState.included.has(recording.rec)){
  tmState.included.add(recording.rec);const reloaded=tmSeq.b+1;await tmSelectB(primaryLap);
  if(profile!==tmState.profile||primary!==tmState.b||reloaded!==tmSeq.b)return;
 }
 const target=(tmState.pool?.laps||[]).find(l=>tmKey(l)===key);
 if(!target){tmErr('The saved comparison reference is unavailable in the current telemetry pool.');return;}
 tmState.a=key;tmFillPickers(tmState.lapB);tmRenderLaps();await tmLoadPair();
 if(item.location_m!=null){const chart=tmState.chart;if(chart){const rect=$id('tm-charts').getBoundingClientRect();tmHover({clientX:rect.left+chart.X(Math.min(item.location_m,chart.maxD))/chart.W*rect.width});} }
}
function reset(profile){state.profile=profile;state.generation++;state.selection=null;state.preview=null;state.job=null;state.lastJob=null;state.run=null;state.previewRequest=null;state.startBusy=false;state.machineBusy=false;state.cancelJob=null;['ai-report','ai-history','ai-preview','ai-laps','ai-references','ai-selection-status','ai-run-status'].forEach(id=>say(id,''));$id('ai-start').disabled=true;$id('ai-export-preview').disabled=true;$id('ai-preview-button').disabled=true;$id('ai-preview-button').textContent='Preview selected data';feedback('idle','Select laps, review the package, then run','Preparation reads local telemetry. Only Start this analysis invokes the selected provider.');if(profile&&currentView==='telemetry'){status();history();}}
function init(){
 const settingsCard=node('section',null,document.querySelector('[data-view="settings"]'));settingsCard.id='ai-settings';settingsCard.className='card rcai';
 settingsCard.innerHTML=`<h3>Optional AI telemetry analysis</h3><p>Uses your existing Codex or Claude Code subscription CLI. Install and sign in externally. Racecast never installs an agent or selects API billing.</p>
 <label><input id="ai-enabled" type="checkbox"> Enable analysis on this machine</label> <button id="ai-save-enabled" disabled>Save enable / disable</button>
 <p>Create a named configuration, save it, then check the CLI / login.</p>
 <div class="rcai-grid"><label>Named configurations<select id="ai-configs"></select></label><label>Name (required)<input id="ai-name" maxlength="200" placeholder="e.g. Codex telemetry"></label><label>Provider<select id="ai-provider"><option value="codex">Codex</option><option value="claude">Claude Code</option></select></label><label>Executable (optional)<input id="ai-executable" placeholder="CLI command or full executable path"></label><label>Default model<input id="ai-config-model" list="ai-model-suggestions" placeholder="Exact model name for this provider"></label><label>Timeout (seconds)<input id="ai-timeout" type="number" min="10" max="3600" step="1" value="600" required></label><label>Agent mode<select id="ai-mode"><option value="isolated">Isolated</option><option value="personal">Personal</option></select></label></div>
 <p id="ai-mode-help"></p>
 <p>Personal content must be chosen explicitly. MCP servers cannot be confined to the analysis package; use a manual run externally when needed. Selective personal Claude customizations are currently unavailable for background jobs.</p>
 <label><input id="ai-instructions" type="checkbox"> Include personal instructions</label> <label><input id="ai-skills" type="checkbox"> Include personal skills</label> <label><input id="ai-mcp" type="checkbox"> Request personal MCP</label>
 <div class="row rcai-settings-actions"><button id="ai-save" disabled>Save new configuration</button><button id="ai-check" disabled>Check CLI / login</button><button id="ai-remove" disabled>Remove configuration</button><p id="ai-settings-status" role="status" aria-live="polite" aria-atomic="true"></p></div><p id="ai-probe" role="status"></p><datalist id="ai-model-suggestions"></datalist><datalist id="ai-run-model-suggestions"></datalist>`;
 const card=node('section',null,document.querySelector('[data-view="telemetry"]'));card.id='ai-analysis';card.className='card rcai';
 card.innerHTML=`<h3>Post-session AI analysis</h3><p id="ai-disabled"></p>
 <ol class="rcai-steps" aria-label="Analysis workflow"><li>Select laps</li><li>Review package</li><li>Run & check report</li></ol>
 <section id="ai-feedback" class="rcai-feedback" data-kind="idle" aria-label="Analysis progress"><div><h4 id="ai-feedback-title">Select laps, review the package, then run</h4><p id="ai-run-status" role="status" aria-live="polite">Preparation reads local telemetry. Only Start this analysis invokes the selected provider.</p><p id="ai-run-meta"></p><progress id="ai-progress" hidden aria-label="Analysis in progress"></progress></div><div class="row"><button id="ai-view-report" hidden>View report</button><button id="ai-new-run" hidden>Prepare a new run</button></div></section>
 <div class="row"><button id="ai-load-selection">Prepare analysis selection</button><button id="ai-history-refresh">Refresh history</button><button id="ai-cancel" disabled>Cancel active analysis</button></div><div id="ai-controls" hidden>
 <p id="ai-selection-status" role="status"></p><div class="rcai-grid"><label>Completed GT7 session<select id="ai-session"></select></label><label>Template<select id="ai-template"><option value="session-overview">Session overview</option><option value="driving-technique">Driving technique</option><option value="consistency">Consistency</option></select></label><label>Agent<select id="ai-agent"></select></label><label>Model<input id="ai-model" list="ai-run-model-suggestions"></label><label>Report language<select id="ai-language"><option value="en">English</option><option value="de">Deutsch</option></select></label></div>
 <h4>Completed laps</h4><p id="ai-lap-count" class="sub"></p><div id="ai-laps" class="rcai-options"></div><details class="rcai-references"><summary>Optional compatible references · none selected automatically</summary><div id="ai-references" class="rcai-options"></div></details><div class="rcai-grid"><label>Practice goal<textarea id="ai-goal" maxlength="4000"></textarea></label><label>Additional questions<textarea id="ai-questions" maxlength="4000"></textarea></label></div>
 <div class="row rcai-actions"><button id="ai-preview-button" disabled>Preview selected data</button><button id="ai-start" disabled>Start this analysis</button><button id="ai-export-preview" disabled>Export preview package</button></div><div id="ai-preview" aria-busy="false"></div></div>
 <p class="sub">One analysis at a time on this machine. Cancellation does not recover consumed subscription quota.</p><details class="rcai-history"><summary>Saved analysis history</summary><div id="ai-history"></div></details><div id="ai-report"></div>`;
 const style=node('style',`.rcai-open-report{display:inline-block;background:var(--accent-strong);color:var(--txt);border-radius:6px;padding:10px 16px;margin:8px 0;font-weight:600;text-decoration:none}#workspace-ai,#ai-controls,#ai-report,#ai-feedback{scroll-margin-top:140px}.rcai{overflow-wrap:anywhere}.rcai-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:14px 0}.rcai-grid label{display:flex;flex-direction:column;gap:6px;min-width:0}.rcai input:not([type=checkbox]),.rcai select,.rcai textarea{width:100%;min-width:0;padding:8px;background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:6px;font:inherit}.rcai textarea{min-height:80px}.rcai-options{display:flex;flex-wrap:wrap;gap:12px}.rcai pre{white-space:pre-wrap;font-size:12px}.rcai article{border-top:1px solid var(--line);padding-top:10px}.rcai button{margin:4px}`);document.head.append(style);
 node('style',`#ai-save{background:var(--accent-strong);border-color:var(--accent-strong);font-weight:600}.rcai-settings-actions #ai-settings-status{flex-basis:100%;margin:0 4px;padding:10px 12px;border:1px solid var(--line);border-radius:6px}.rcai-settings-actions [data-kind=error]{color:var(--bad-text);border-color:var(--bad)!important}.rcai-settings-actions [data-kind=success]{color:#4ADE80}.rcai-settings-actions [data-kind=pending]{color:#FCD34D}.rcai input:disabled{opacity:.5}`,document.head);
 node('style',`.rcai-steps{display:flex;gap:32px;padding-left:24px;color:var(--muted);font-size:13px}.rcai-feedback{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 20px;margin:18px 0;border:1px solid var(--line);border-left:4px solid #73839f;border-radius:8px;background:var(--card2)}.rcai-feedback h4{font-size:18px;margin:0 0 6px}.rcai-feedback p{margin:4px 0;line-height:1.5}#ai-run-meta{color:var(--muted);font-size:13px}.rcai-feedback[data-kind=ready],.rcai-feedback[data-kind=completed]{border-left-color:#4ade80}.rcai-feedback[data-kind=failed],.rcai-feedback[data-kind=error],.rcai-feedback[data-kind=timed_out],.rcai-feedback[data-kind=interrupted]{border-left-color:#fb7185}.rcai-feedback[data-kind=running],.rcai-feedback[data-kind=preparing],.rcai-feedback[data-kind=validating],.rcai-feedback[data-kind=starting]{border-left-color:#60a5fa}#ai-progress{width:220px;max-width:100%;height:5px;margin-top:10px;accent-color:#60a5fa}.rcai #ai-laps{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:8px}.rcai-lap{display:flex;align-items:center;gap:7px;border:1px solid var(--line);border-radius:6px;padding:9px;font-size:13px}.rcai-lap input{margin:0}.rcai-excluded{opacity:.6;flex-wrap:wrap}.rcai-excluded small{flex-basis:100%;font-size:11px}.rcai details{margin:14px 0;border:1px solid var(--line);border-radius:6px;padding:12px}.rcai summary{cursor:pointer;font-weight:500}.rcai details p{line-height:1.55}.rcai #ai-references{display:block}.rcai fieldset{border:1px solid var(--line);margin:12px 0;display:flex;flex-wrap:wrap;gap:12px}.rcai fieldset label{display:flex;align-items:center;gap:6px}.rcai legend{font-size:12px;color:var(--muted);padding:0 6px}.rcai-package-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:18px 0}.rcai-package-metrics>div{display:flex;flex-direction:column;gap:5px;padding:14px;border:1px solid var(--line);border-radius:6px;background:var(--card2)}.rcai-package-metrics strong{font-size:20px}.rcai-package-metrics span{color:var(--muted);font-size:12px}#ai-start{background:var(--accent-strong);border-color:var(--accent-strong);font-weight:600}#ai-start:disabled{opacity:.45}.rcai-actions{padding:8px 0}.rcai pre{max-height:380px;overflow:auto;padding:12px;background:var(--card2);border-radius:6px}.rcai article{padding:16px;margin:12px 0;background:var(--card2);border:1px solid var(--line);border-radius:8px}@media(max-width:600px){.rcai-steps{gap:18px;font-size:11px}.rcai-feedback{align-items:flex-start;flex-direction:column}.rcai-feedback h4{font-size:16px}.rcai-actions{display:grid;gap:6px}.rcai-actions button{margin:0}}`,document.head);
 $id('ai-settings').querySelectorAll('input,select').forEach(e=>{if(!['ai-configs','ai-enabled'].includes(e.id))e.addEventListener(e.tagName==='SELECT'||e.type==='checkbox'?'change':'input',settingsChanged);});
 $id('ai-enabled').onchange=()=>{settingsControls();settingsStatus('Unsaved enable setting. Click Save enable / disable.','pending');};
 $id('ai-save-enabled').onclick=()=>saveSettings(false,true);
 $id('ai-provider').addEventListener('change',()=>{$id('ai-config-model').value='';modelSuggestions();});
 $id('ai-configs').onchange=()=>{state.settingsDirty=false;populateConfig(state.settings?.agents.find(a=>a.id===$id('ai-configs').value));settingsStatus($id('ai-configs').value?'Saved configuration loaded.':'Enter a name, choose a provider, then save the configuration.');};
 $id('ai-save').onclick=()=>saveSettings();$id('ai-remove').onclick=()=>saveSettings(true);$id('ai-check').onclick=probe;
 $id('ai-load-selection').onclick=selection;$id('ai-session').onchange=renderLaps;$id('ai-preview-button').onclick=preview;$id('ai-start').onclick=start;$id('ai-export-preview').onclick=exportPreview;$id('ai-cancel').onclick=cancel;$id('ai-history-refresh').onclick=history;
 $id('ai-new-run').onclick=prepareNewRun;$id('ai-view-report').onclick=()=>{if(typeof showView==='function')showView('reports');$id('ai-report').scrollIntoView({block:'start',behavior:'smooth'});};
 ['ai-model','ai-template','ai-goal','ai-questions','ai-language'].forEach(id=>$id(id).oninput=invalidate);
 $id('ai-agent').onchange=()=>{const a=state.settings?.agents.find(a=>a.id===$id('ai-agent').value);$id('ai-model').value=a?.model||'';modelSuggestions();invalidate();};
 window.RacecastAI={settings,selection,ensureSelection,reset,status,history};settings().then(()=>{if(tmState.profile&&currentView==='telemetry'){ensureSelection();status();history();}});
 setInterval(()=>{if(currentView==='telemetry'||currentView==='reports')status();},1500);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();

/* Shared driver radio controls. Native audio belongs to the relay on the driver's PC. */
(function () {
'use strict';
const CSS=`:host{display:block;color:#edf3fc;font:14px/1.5 system-ui,sans-serif;--edge:#41536b;--muted:#b7c7dc;accent-color:#a9ccff;color-scheme:dark}*{box-sizing:border-box}[hidden]{display:none!important}h2,h3,p{margin:0 0 10px}h2{font-size:19px}h3{font-size:15px}a{color:#a9ccff}button,input,select,textarea{font:inherit;color:inherit;background:#101a28;border:1px solid #61758f;border-radius:6px;padding:8px;max-width:100%}button{cursor:pointer;min-height:38px}button:hover{border-color:#a9ccff}button:disabled{opacity:.55;cursor:default}input[type=checkbox]{width:auto}input[type=range]{width:130px;padding:0}textarea{width:100%;min-height:58px}label{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.card{border:1px solid var(--edge);border-radius:10px;background:#151e2b;padding:16px;margin-bottom:14px}.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.head{display:flex;justify-content:space-between;gap:12px;align-items:center}.muted,small{color:var(--muted)}.error{color:#ffb9b9}.presets{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:12px 0}.answer{font-size:17px;line-height:1.55;overflow-wrap:anywhere}.urgent{border-color:#ff9b9b;background:#301c26}.hint{border-top:1px solid var(--edge);padding-top:8px;margin-top:4px}.hint p{margin:2px 0 0}.badge{border:1px solid var(--edge);border-radius:14px;padding:2px 9px;color:#bce5cb;white-space:nowrap}details{margin-top:12px}summary{cursor:pointer;color:#b8d4ff}table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--edge);padding:8px;overflow-wrap:anywhere}th{width:43%;font-weight:500;color:var(--muted)}ol{list-style:none;padding:0;margin:0}li{padding:14px 0;border-bottom:1px solid var(--edge)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid label{display:flex;flex-direction:column;align-items:stretch;margin:10px 0}.grid .check{flex-direction:row;align-items:center}form button{margin-top:10px}.compact .presets{grid-template-columns:1fr 1fr}.compact .card{margin:0;border:0;border-radius:0}.compact .answer{font-size:14px}.compact .notice{font-size:12px}.notice{margin:10px 0;color:var(--muted)}.coach,.feature,.setting{border:1px solid var(--edge);border-radius:8px;padding:14px;margin:0 0 18px;background:#101a28}.off{font-size:15px;margin:4px 0 0}.cards{display:grid;grid-template-columns:1fr 1fr;gap:14px}.setting{margin:0}.setting.voice{grid-column:1/-1}.setting label{display:flex;flex-direction:column;align-items:stretch;margin:10px 0}.setting .check{flex-direction:row;align-items:center}.compact .feature{padding:10px}.coach-controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}.coach-controls label{align-items:flex-start;flex-direction:column}.coach-controls label:has(input[type=checkbox]){flex-direction:row}.coach select{width:100%}.coach .row{margin:10px 0}.compact .coach{padding:10px}.coach .answer{font-size:16px}.coach summary,.coach p{overflow-wrap:anywhere}.coach #coach-history-entries details{margin-bottom:10px}.coach #coach-history-delete-all{margin-top:10px}@media(max-width:650px){.presets,.grid{grid-template-columns:1fr 1fr}.grid,.cards{grid-template-columns:1fr}.head{flex-wrap:wrap}.card{padding:13px}.answer{font-size:15px}}`;
const PRESETS=[['fuel','Fuel status'],['pace','Pace'],['tyres','Tyre temperatures'],['status','Short status']];
function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
function val(n,d=1){return typeof n==='number'&&Number.isFinite(n)?n.toFixed(d):'Unavailable';}
function same(a,b){return JSON.stringify(a)===JSON.stringify(b);}
const COMPOUNDS=['CH','CM','CS','SH','SM','SS','RH','RM','RS','IM','W','D','S'];
// Each AI feature owns one section; a switched-off feature's section says so instead of
// hiding the other one.
const OFF='Off · enable it in the Control Center';
function disabled(error){return error.status===403&&error.code==='feature_disabled';}
function sourceOf(message){return message.source==='coach'||message.source==='engineer'?message.source:message.kind==='coach'?'coach':'engineer';}
// A refusal is the hint that its feature is switched off, not advice: the switched-off
// feature's own section only says Off, so the hint goes to the shared Voice row.
function bySource(messages){const split={engineer:[],coach:[]};for(const message of messages||[])if(message.kind!=='refusal')split[sourceOf(message)].push(message);return split;}
const KINDS={question:'Voice question',requested:'Requested answer',refusal:'Feature off',coach:'Coach message'};
function messageItem(message){const li=el('li');li.append(el('small',(KINDS[message.kind]||'Automatic advice')+' · '+message.state));if(message.question)li.append(el('p',message.question,'muted'));li.append(el('p',message.text));if(message.explanation)li.append(el('p','Interpretation: '+message.explanation,'muted'));return li;}
function agentChoices(select,agents,chosen){select.replaceChildren(new Option('Off',''));for(const row of agents||[])select.append(new Option(row.name,row.id));if(chosen&&!Array.from(select.options).some(o=>o.value===chosen))select.append(new Option('Unavailable configured agent',chosen));select.value=chosen||'';}
class View {
 constructor(host,options={}){
  this.host=host;this.compact=!!options.compact;this.admin=!!options.admin;this.dead=false;this.busy=false;this.sending=false;this.controllers=new Set();this.state=null;this.native=null;this.setup=null;this.formBase=null;this.dirty=false;this.editVersion=0;this.generation=0;this.timer=null;this.access=false;this.actionError='';
  this.root=host.shadowRoot||host.attachShadow({mode:'open'});this.root.innerHTML=`<style>${CSS}</style><div class="${this.compact?'compact':''}"><section class="card"><div class="head"><h2>${this.admin?'Driver radio setup':'Driver radio'}</h2>${this.admin?'<span id="live" class="badge">Connecting</span>':''}</div><p id="problem" class="error" role="status"></p><button id="reload" type="button" hidden>Reload current form</button><p id="sign" hidden><a href="/console">Sign in to Console</a> to use driver radio.</p><div id="content" hidden></div></section></div>`;
  this.content=this.q('content');this.problem=this.q('problem');this.q('sign').hidden=true;this.q('reload').onclick=()=>{this.dirty=false;this.editVersion++;this.formBase=null;this.actionError='';clearTimeout(this.timer);this.poll();};
  if(this.admin)this.buildAdmin();else this.buildDriver();
  this.coach=new CoachView(this.q('coach-section'),{admin:this.admin,compact:this.compact,profile:()=>this.adminData?.profile||null});
  this.poll();this.onHide=()=>this.destroy();window.addEventListener('pagehide',this.onHide,{once:true});
 }
 q(id){return this.root.getElementById(id);}
 async api(path,body){
  const controller=new AbortController();this.controllers.add(controller);const timeout=setTimeout(()=>controller.abort(),4000);
  try{const response=await fetch((this.admin?'/api/driver-radio':'/console/driver-radio')+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
   const data=await response.json();if(!response.ok||data.ok===false){const error=new Error(data.error?.message||data.error||'Driver radio unavailable');error.status=response.status;error.code=data.error?.code||data.code;throw error;}return data;
  }finally{clearTimeout(timeout);this.controllers.delete(controller);}
 }
 deny(error){this.coach?.clear(error.message);this.generation++;this.editVersion++;this.access=false;this.content.hidden=true;this.content.querySelectorAll('button').forEach(n=>n.disabled=true);if(this.q('latest'))this.q('latest').textContent='';if(this.q('history'))this.q('history').replaceChildren();if(this.q('evidence'))this.q('evidence').replaceChildren();this.state=null;this.native=null;this.setup=null;this.adminData=null;this.formBase=null;this.dirty=false;for(const input of this.content.querySelectorAll('input,select,textarea')){input.value='';input.checked=false;}if(this.q('question'))this.q('question').textContent='';this.problem.textContent=error.status===401?'Sign in to use driver radio.':error.status===403?(error.message||'Ask the producer to enable the AI Race Engineer or the AI Coach for this profile.'):error.message||'Relay connection unavailable.';this.q('sign').hidden=this.admin||error.status!==401;this.q('live').textContent='Unavailable';this.coach?.messages(null);}
 // The AI Race Engineer is switched off: drop its data like a denial, but only in its own
 // section, so the AI Coach section keeps working.
 engineerOff(){
  this.generation++;this.editVersion++;this.state=null;this.setup=null;this.formBase=null;this.dirty=false;this.actionError='';
  const body=this.q('engineer-body');body.querySelectorAll('button').forEach(n=>n.disabled=true);for(const input of body.querySelectorAll('input,select,textarea')){input.value='';input.checked=false;}
  for(const id of ['latest','question','attachment'])this.q(id).textContent='';this.q('history')?.replaceChildren();this.q('evidence')?.replaceChildren();
  this.content.hidden=false;this.q('sign').hidden=true;this.problem.textContent='';this.q('reload').hidden=true;this.engineer(false);this.q('live').textContent='Off';
 }
 engineer(on){this.q('engineer-off').hidden=on;this.q('engineer-body').hidden=!on;}
 // Both AI features are off: the shared voice row and the coach messages go too.
 voiceOff(){this.native=null;this.access=false;this.q('voice-section').hidden=true;this.renderHint(null);this.q('phase').textContent='';for(const id of ['pause','mute','volume','language'])this.q(id).disabled=true;this.coach?.messages(null);}
 async poll(){
  if(this.dead||this.busy)return;this.busy=true;const generation=this.generation;let voiceOk=false;
  try{if(this.admin){const data=await this.api('');if(this.dead||generation!==this.generation)return;this.renderAdmin(data);}else{
   // The message lane and native voice serve either AI feature; snapshot and setup only the
   // AI Race Engineer. One switched-off feature never takes the other's data with it.
   const [shared,facts]=await Promise.allSettled([Promise.all([this.api('/messages'),this.api('/native')]),Promise.all([this.api('/snapshot'),this.api('/setup')])]);
   if(this.dead||generation!==this.generation)return;
   for(const part of [shared,facts])if(part.status==='rejected'&&!disabled(part.reason)&&(part.reason.status===401||part.reason.status===403))throw part.reason;
   this.content.hidden=false;this.q('sign').hidden=true;
   if(shared.status==='rejected'){if(!disabled(shared.reason))throw shared.reason;this.voiceOff();this.engineerOff();return;}
   const [history,native]=shared.value,split=bySource(history.messages||[]);
   this.native=native;this.access=true;this.renderVoice();this.renderHint(history);voiceOk=true;this.coach.messages(split.coach);
   if(facts.status==='rejected'){if(!disabled(facts.reason))throw facts.reason;this.engineerOff();return;}
   const [state,setup]=facts.value;
   if(state.epoch!==history.epoch||!same(state.binding,history.binding)||!same(state.binding,setup.binding))throw new Error('Session changed. Refreshing current radio facts.');
   const changed=this.state&&!same(this.state.binding,state.binding);if(changed){this.generation++;this.editVersion++;this.dirty=false;this.formBase=null;}
   this.state=state;this.setup=setup;this.problem.textContent=this.actionError;this.q('reload').hidden=!this.actionError;this.renderDriver(split.engineer);
  }}catch(error){if(!this.dead){if(!this.admin&&disabled(error))this.engineerOff();else if(error.status===401||error.status===403)this.deny(error);else if(generation===this.generation){this.access=false;if(this.admin)this.content.hidden=true;else{this.content.hidden=false;this.q('engineer-off').hidden=true;this.q('engineer-body').hidden=true;if(!voiceOk)this.q('voice-section').hidden=true;}this.problem.textContent=error.message||'Relay connection unavailable.';this.q('live').textContent='Unavailable';}}}finally{this.busy=false;if(!this.dead)this.timer=setTimeout(()=>this.poll(),this.access?1000:4000);}
 }
 async send(path,body){
  if(this.sending)return;
  const savedForm=this.admin?path==='':path==='/setup';const editVersion=this.editVersion;const generation=this.generation;
  this.sending=true;this.actionError='';this.problem.textContent='';
  try{
   const result=await this.api(path,body);
   if(!this.dead&&generation===this.generation&&savedForm){
    if(this.editVersion===editVersion){this.dirty=false;this.formBase=null;}
    else if(this.dirty){this.formBase=this.admin?{...this.adminData,profile:result.profile,settings:result.result}:result;}
   }
   return result;
  }catch(error){
   if(!this.dead){if(!this.admin&&disabled(error)){if(path==='/native')this.voiceOff();this.engineerOff();}else if(error.status===401||error.status===403)this.deny(error);else if(generation===this.generation){this.actionError=error.status===409?'This session or form changed. Reload the form and review its current values before saving.':error.message;this.problem.textContent=this.actionError;this.q('reload').hidden=false;}}
   return null;
  }finally{this.sending=false;if(!this.dead){clearTimeout(this.timer);this.timer=setTimeout(()=>this.poll(),0);}}
 }
 buildDriver(){
  this.content.innerHTML=`<section id="voice-section" class="feature" aria-label="Voice" hidden><div class="head"><h3>Voice</h3></div><div class="row"><span id="phase"></span><button id="pause" type="button">Pause listening</button><label><input id="mute" type="checkbox">Mute replies</label><label>Volume<input id="volume" type="range" min="0" max="1" step="0.05"></label><label>Input<select id="language"><option value="en">English</option><option value="de">German</option></select></label></div><p class="notice">Both AI features speak through this voice. Audio runs on the relay PC. OBS mute does not pause listening.</p><div id="voice-hint" class="hint" role="status" hidden></div></section><section id="engineer-section" class="feature" aria-label="AI Race Engineer"><div class="head"><h3>AI Race Engineer</h3><span id="live" class="badge">Connecting</span></div><p id="engineer-off" class="muted off" hidden>${OFF}</p><div id="engineer-body"><div class="presets"></div><div id="latest-box"><p id="question" class="muted"></p><p id="latest" class="answer" role="status">Waiting for a radio message.</p><small id="attachment"></small></div></div></section><section id="coach-section" aria-label="AI Coach"></section>`;
  const body=this.q('engineer-body');const presets=this.content.querySelector('.presets');for(const [key,label] of PRESETS){const button=el('button',label);button.type='button';button.dataset.preset=key;button.onclick=()=>this.state&&this.send('/request',{preset:key,binding:this.state.binding});presets.append(button);}
  this.q('pause').onclick=()=>this.control({paused:!this.native.paused});this.q('mute').onchange=e=>this.control({muted:e.target.checked});this.q('volume').onchange=e=>this.control({volume:Number(e.target.value)});this.q('language').onchange=e=>this.control({input_language:e.target.value});
  if(this.compact){const link=el('a','Open driver desk');link.href='/console/driver-radio';body.append(link);return;}
  const detail=el('details');detail.append(el('summary','Measurement evidence'));const table=el('table');table.id='evidence';detail.append(table);body.append(detail);
  body.insertAdjacentHTML('beforeend',`<div class="grid"><form id="setup-form"><h3>Optional race setup</h3><label>Race length<select id="race-mode"><option value="">Unknown</option><option value="laps">Laps</option><option value="duration_min">Minutes</option></select></label><label>Length<input id="race-length" type="number" min="1" max="100000" step="1"></label><label>Starting compound<select id="compound"><option value="">Unknown</option></select></label><label>Fuel reserve · litres<input id="reserve" type="number" min="0" max="1000" step="0.1"></label><button type="submit">Save setup</button><p class="muted">Missing setup still allows measured status answers.</p></form><form id="service-form"><h3>Confirm a tyre change</h3><label>Fitted compound<select id="service-compound"></select></label><button type="submit">Confirm tyre change</button><p class="muted">Confirm only a completed change. An inferred stop does not confirm new tyres.</p></form></div><h3 style="margin-top:20px">AI Race Engineer messages</h3><ol id="history"></ol><p class="notice">Questions may be audible through your broadcast microphone. Closing this desk does not stop the enabled native service.</p>`);
  for(const value of COMPOUNDS){this.q('compound').append(new Option(value,value));this.q('service-compound').append(new Option(value,value));}
  this.q('setup-form').oninput=()=>{if(!this.dirty)this.formBase=this.setup;this.dirty=true;this.editVersion++;};
  this.q('setup-form').onsubmit=e=>{e.preventDefault();const base=this.formBase||this.setup;if(!base)return;const setup={};const mode=this.q('race-mode').value;if(mode)setup[mode]=Number(this.q('race-length').value);if(this.q('compound').value)setup.starting_compound=this.q('compound').value;if(this.q('reserve').value!=='')setup.fuel_reserve_l=Number(this.q('reserve').value);this.send('/setup',{binding:base.binding,expected:base.revision,setup});};
  this.q('service-form').onsubmit=e=>{e.preventDefault();if(this.setup)this.send('/service',{binding:this.setup.binding,expected:this.setup.revision,compound:this.q('service-compound').value});};
 }
 renderVoice(){
  this.q('voice-section').hidden=false;
  this.q('phase').textContent='Native voice: '+(this.native.error?this.native.phase+' · '+this.native.error.replaceAll('_',' '):this.native.phase);this.q('pause').textContent=this.native.paused?'Resume listening':'Pause listening';
  for(const id of ['pause','mute','volume','language'])this.q(id).disabled=!this.native.enabled;
  if(this.root.activeElement!==this.q('volume'))this.q('volume').value=this.native.volume;this.q('mute').checked=this.native.muted;this.q('language').value=this.native.input_language;
 }
 // The newest message of the current session, when it is a refusal: the hint answers that
 // one voice request and gives way to the next message of either feature.
 renderHint(history){const box=this.q('voice-hint');const list=history?.messages||[],last=list[list.length-1];const hint=last?.kind==='refusal'&&last.epoch===history.epoch?last:null;box.replaceChildren();box.hidden=!hint;if(hint)box.append(el('small',KINDS.refusal),el('p',hint.text));}
 control(change){if(this.native)this.send('/native',{binding:this.native.binding,expected:this.native.revision,...change});}
 renderDriver(messages){
  this.engineer(true);this.q('engineer-body').querySelectorAll('button').forEach(n=>n.disabled=false);this.q('live').textContent=this.state.live?'Live telemetry':'Live data unavailable';
  const radioMessages=messages;
  const latest=radioMessages[radioMessages.length-1];this.q('latest-box').classList.toggle('urgent',latest?.severity==='urgent');this.q('latest').textContent=latest?.text||'No session messages yet. Choose a preset.';this.q('question').textContent=latest?.question?'You asked: '+latest.question:'';
  this.q('attachment').textContent=latest?((latest.audio_state==='failed'?'Speech failed; text is retained. ':latest.audio_state==='unconfigured'?'English speech is not configured. ':'')+(latest.explanation?'Interpretation: '+latest.explanation:'')):'';
  if(this.compact)return;
  const history=this.q('history');history.replaceChildren();for(const message of radioMessages.slice(-32).reverse())history.append(messageItem(message));
  const facts=latest?.evidence||this.state.assessment||{};const fuel=facts.fuel||{},pace=facts.pace||{},tyres=facts.tyres||{};const table=this.q('evidence');table.replaceChildren();
  for(const [label,value] of [['Fuel · litres',val(fuel.current_l)],['Consumption · litres/lap',val(fuel.consumption_l,3)+' · '+(fuel.samples||0)+' comparable laps'],['Range after reserve · laps',val(fuel.usable_laps)],['Finish estimate',(fuel.finish||'unknown').replaceAll('_',' ')+(fuel.finish_assumption?' · '+fuel.finish_assumption:'')],['Pace trend',(pace.state||'unavailable')+' · '+val(pace.delta_s,3)+' seconds'],['Last eligible comparable lap · seconds',val(pace.last_s,3)],['Best eligible comparable lap · seconds',val(pace.best_s,3)],['Comparable laps',(pace.baseline_ids?.length||0)+' baseline / '+(pace.recent_ids?.length||0)+' recent'],['Tyres',(tyres.compound||'Unknown compound')+' · wear is not measured'],['Confirmed tyre age · completed laps',val(tyres.age_laps,0)]]){const row=el('tr');row.append(el('th',label),el('td',value));table.append(row);}
  for(const note of facts.uncertainties||[]){const row=el('tr');row.append(el('th','Uncertainty'),el('td',note));table.append(row);}
  if(!this.dirty){const setup=this.setup.setup||{};this.q('race-mode').value=setup.laps!==undefined?'laps':setup.duration_min!==undefined?'duration_min':'';this.q('race-length').value=setup.laps??setup.duration_min??'';this.q('compound').value=setup.starting_compound||'';this.q('reserve').value=setup.fuel_reserve_l??'';}
  this.q('setup-form').querySelector('button').disabled=!this.setup.binding;this.q('service-form').querySelector('button').disabled=!this.setup.binding;
 }
 buildAdmin(){
  this.content.innerHTML=`<form id="admin-form"><div class="cards"><section class="setting" aria-label="AI Race Engineer settings"><h3>AI Race Engineer</h3><label class="check"><input id="ai-engineer-enabled" type="checkbox">Enable AI Race Engineer</label><label>Optional live AI<select id="ai-engineer-agent"><option value="">Off</option></select></label><small>Fuel, pace and tyre presets, proactive fuel and pace notices. Runs while live telemetry is present.</small></section><section class="setting" aria-label="AI Coach settings"><h3>AI Coach</h3><label class="check"><input id="ai-coach-enabled" type="checkbox">Enable AI Coach</label><label>Optional live AI<select id="ai-coach-agent"><option value="">Off</option></select></label><small>Per-lap driving feedback. Training still starts only with Start training.</small></section><section class="setting voice" aria-label="Voice settings"><h3>Voice</h3><div class="grid"><div><label class="check"><input id="voice-enabled" type="checkbox">Enable native voice with this profile's relay</label><label>Microphone override<input id="microphone" placeholder="Use the Control Center selected microphone"></label><label>Default input language<select id="input-language"><option value="en">English</option><option value="de">German</option></select></label></div><div><label>Prepared wake phrase<select id="wake"></select></label><p id="models" class="muted"></p><label>Transcription<select id="stt"><option value="vosk">Local Vosk</option><option value="whisper">Local whisper.cpp</option><option value="google">Google Speech, configured cloud</option></select></label><small>Cloud recognition uploads only the wake-gated question buffer. No automatic fallback.</small><label class="check"><input id="speech" type="checkbox">Send answer text to Google for English Studio-Q speech</label></div></div><small>Both AI features speak and listen through this one voice.</small></section></div><p class="muted" style="margin-top:12px">Anyone signed in to this profile's Console can use an enabled feature.</p><button type="submit">Save profile radio settings</button></form><p id="native-summary" class="muted"></p><p class="notice">Native voice keeps running with browsers and Control Center closed. OBS software mute does not pause listening. Questions may be audible in the broadcast. Output uses the PC audio route.</p><section id="coach-section" aria-label="AI Coach training"></section>`;
  this.q('admin-form').oninput=()=>{if(!this.dirty)this.formBase=this.adminData;this.dirty=true;this.editVersion++;};
  this.q('admin-form').onsubmit=e=>{e.preventDefault();if(!this.adminData)return;const base=this.formBase||this.adminData;const old=base.settings;const fields={expectedProfile:base.profile,expected:old.revision,features:{engineer:{enabled:this.q('ai-engineer-enabled').checked,agent:this.q('ai-engineer-agent').value||null},coach:{enabled:this.q('ai-coach-enabled').checked,agent:this.q('ai-coach-agent').value||null}},voice:{enabled:this.q('voice-enabled').checked,microphone:this.q('microphone').value.trim()||null,input_language:this.q('input-language').value},native:{...old.native,wake:this.q('wake').value||old.native.wake,stt:this.q('stt').value},providers:{speech:this.q('speech').checked}};this.send('',fields);};
 }
 renderAdmin(data){
  if(this.adminData?.profile!==data.profile){this.coach?.clear(this.adminData?'Profile changed. Refreshing coaching.':'');this.generation++;this.editVersion++;this.dirty=false;this.formBase=null;}
  this.access=true;this.content.hidden=false;this.problem.textContent='';this.adminData=data;this.problem.textContent=this.actionError;this.q('reload').hidden=!this.actionError;this.q('live').textContent=data.profile;
  this.q('native-summary').textContent='Native voice: '+data.native.phase+(data.native.error?' · '+data.native.error.replaceAll('_',' '):'');
  if(this.dirty)return;const s=data.settings;this.q('ai-engineer-enabled').checked=s.features.engineer.enabled;this.q('ai-coach-enabled').checked=s.features.coach.enabled;this.q('voice-enabled').checked=s.voice.enabled;this.q('microphone').value=s.voice.microphone||'';this.q('input-language').value=s.voice.input_language;this.q('speech').checked=s.providers.speech;this.q('stt').value=s.native.stt;
  const wake=this.q('wake');wake.replaceChildren();for(const id of data.native.phrases||[])wake.append(new Option(id==='hey_jeff'?'Hey Jeff':'Hey Engineer',id));wake.disabled=!wake.options.length;wake.value=s.native.wake;this.q('models').textContent=wake.options.length?'Prepared local wake models are available.':'Local models are unavailable. Run racecast radio provision before using native voice.';
  for(const feature of ['engineer','coach'])agentChoices(this.q('ai-'+feature+'-agent'),data.agents,s.features[feature].agent);
  this.q('admin-form').querySelectorAll('button').forEach(n=>n.disabled=false);
 }
 destroy(){this.coach?.destroy();this.dead=true;clearTimeout(this.timer);for(const c of this.controllers)c.abort();this.controllers.clear();window.removeEventListener('pagehide',this.onHide);}
}
class CoachView {
 constructor(host,{admin=false,compact=false,profile=()=>null}={}){
  this.host=host;this.admin=admin;this.compact=compact;this.profile=profile;this.off=false;this.state=null;this.scope=null;this.serviceKey=null;this.requestSerial=0;this.adoptedSerial=0;this.historyFloor=0;this.retired=new Set();this.generation=0;this.editVersion=0;this.base=null;this.dirty=false;this.sending=false;this.polling=false;this.stopping=false;this.dead=false;this.actionError='';this.timer=null;this.controllers=new Set();
  host.className='coach';host.innerHTML=`<div class="head"><h3>${admin?'AI Coach training':'AI Coach'}</h3><span id="coach-phase" class="badge">Connecting</span></div><p id="coach-off" class="muted off" hidden>${admin?'Off · enable the AI Coach above and save':OFF}</p><div id="coach-body"><p id="coach-error" class="error" role="status"></p><div class="coach-controls"><label>Mode<select id="coach-mode"><option value="practice">Practice</option><option value="time_trial">Time trial</option></select></label><label>Objective<select id="coach-objective"><option value="balanced">Balanced</option><option value="consistency">Consistency</option><option value="pace">Pace</option></select></label><label><input id="coach-ai" type="checkbox">Optional AI explanation</label></div><p class="notice">Optional AI uses the AI Coach agent chosen in the Control Center. Measured feedback does not wait for it.</p><div class="row"><button id="coach-start" type="button">Start training</button><button id="coach-stop" type="button">Stop training</button><button id="coach-goal" type="button">Apply objective</button><button id="coach-reload" type="button" hidden>Reload current values</button></div><p id="coach-calibration" class="muted"></p><p id="coach-reference" class="muted"></p><p id="coach-label" class="muted"></p><p id="coach-focus" class="answer" role="status"></p><p id="coach-progress" class="muted"></p><p id="coach-review"></p><p id="coach-debrief" class="muted"></p><div class="coach-advanced" ${compact?'hidden':''}><div class="coach-controls"><label>Skill<select id="coach-skill"></select></label><label>Reference<select id="coach-reference-choice"><option value="">Automatic personal reference</option></select></label></div><div class="row"><button id="coach-set-skill" type="button">Apply skill</button><button id="coach-use-reference" type="button">Use reference</button><button id="coach-skip" type="button">Skip exercise</button><button id="coach-keep" type="button">Keep exercise</button><button id="coach-show-evidence" type="button">View measured evidence</button></div><details id="coach-details"><summary>Measured training evidence</summary><table id="coach-evidence"></table></details><p id="coach-history-suggestion" class="muted"></p><button id="coach-apply-history" type="button" hidden>Apply suggested skill</button><details id="coach-history"><summary>Private training history</summary><p id="coach-history-error" class="muted"></p><div id="coach-history-entries"></div><button id="coach-history-delete-all" type="button">Delete all private history</button><p class="notice">Up to 32 summaries and 90 days per driver. Deleting active training also prevents later checkpoints until a fresh Start. Raw audio and traces are not stored here.</p></details></div>${admin||compact?'':'<div id="coach-radio" hidden><h3 style="margin-top:16px">AI Coach messages</h3><ol id="coach-messages"></ol></div>'}</div>`;
  this.q('skill').append(new Option('Any skill',''));for(const [key,label] of [['braking','Braking'],['throttle','Throttle'],['steering','Steering'],['gear','Gear choice'],['speed','Speed'],['line','Line'],['pace','Pace'],['consistency','Consistency']])this.q('skill').append(new Option(label,key));
  for(const id of ['mode','objective','ai','skill','reference-choice'])this.q(id).oninput=()=>{if(!this.dirty)this.base=this.state;this.dirty=true;this.editVersion++;};
  this.q('start').onclick=()=>this.send('start',{mode:this.q('mode').value,objective:this.q('objective').value,allow_ai:this.q('ai').checked});
  this.q('stop').onclick=()=>this.send('stop',{});this.q('goal').onclick=()=>this.send('objective',{objective:this.q('objective').value});
  this.q('set-skill').onclick=()=>this.send('skill',{skill:this.q('skill').value||null});this.q('use-reference').onclick=()=>this.send('reference',{lap_id:this.q('reference-choice').value||null});
  this.q('skip').onclick=()=>this.send('skip',{});this.q('keep').onclick=()=>this.send('keep',{});
  this.q('show-evidence').onclick=()=>{this.q('details').open=true;this.q('details').scrollIntoView({block:'nearest'});};
  this.q('history-delete-all').onclick=()=>this.send('delete_history',{id:null});
  this.q('apply-history').onclick=()=>{if(this.state?.history_suggestion)this.send('skill',{skill:this.state.history_suggestion.skill});};
  this.q('reload').onclick=()=>{this.generation++;this.abort();this.polling=false;this.dirty=false;this.base=null;this.actionError='';this.editVersion++;this.q('reload').hidden=true;this.schedule(0);};
  this.render();this.poll();
 }
 q(id){return this.host.querySelector('#coach-'+id);}
 feature(on){this.off=!on;this.q('off').hidden=on;this.q('body').hidden=!on;if(!on)this.messages(null);}
 // A newer review cancels the previous coach message; show only the few still current.
 messages(list){const box=this.q('radio'),items=this.q('messages');if(!box||!items)return;const current=(list||[]).filter(m=>m.state!=='cancelled').slice(-4).reverse();items.replaceChildren();box.hidden=this.off||!current.length;if(!this.off)for(const message of current)items.append(messageItem(message));}
 abort(){for(const controller of this.controllers)controller.abort();this.controllers.clear();}
 clear(message='Connecting'){
  this.generation++;this.abort();this.state=null;this.scope=null;this.historyFloor=0;this.adoptedSerial=0;this.base=null;this.dirty=false;this.actionError='';this.editVersion++;this.polling=false;this.sending=false;this.stopping=false;this.loadError=false;
  this.q('error').textContent=message;this.q('reference-choice').replaceChildren(new Option('Automatic personal reference',''));this.q('mode').value='practice';this.q('objective').value='balanced';this.q('skill').value='';this.q('ai').checked=false;this.q('reload').hidden=true;this.render();
 }
 schedule(delay=1000){if(this.dead)return;clearTimeout(this.timer);this.timer=setTimeout(()=>{this.timer=null;this.poll();},delay);}
 async api(body){
  const serial=++this.requestSerial;
  const controller=new AbortController();this.controllers.add(controller);const timer=setTimeout(()=>controller.abort(),4000);
  const request=async(url,options={})=>{const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal,...options});const data=await response.json();if(!response.ok||data.ok===false){const error=new Error(data.error?.message||data.error||'Coaching is unavailable.');error.status=response.status;error.code=data.error?.code||data.code;throw error;}return data;};
  try{
   const data=await request((this.admin?'/api/driver-radio':'/console/driver-radio')+'/coach',body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const state=data.result||data;
   if(!this.admin){const identity=await request('/console/whoami');if(!identity.ai_coach){const error=new Error('Enable the AI Coach for this profile');error.status=403;error.code='feature_disabled';throw error;}if(state.viewer!=='console:'+identity.subject){const error=new Error('Console identity changed. Refreshing coaching.');error.code='identity_changed';throw error;}}
   return {...state,_clientSerial:serial};
  }finally{clearTimeout(timer);this.controllers.delete(controller);}
 }
 adopt(state,generation,expectedProfile){
  if(this.dead||generation!==this.generation)return false;
  if(!state||state.version!==1||!Number.isSafeInteger(state.revision)||typeof state.profile!=='string'||typeof state.viewer!=='string'||typeof state.generation!=='string')throw new Error('The current coaching state is unavailable.');
  if(this.admin&&expectedProfile&&this.profile()!==expectedProfile){this.clear('Profile changed. Refreshing coaching.');return false;}
  if(this.admin&&this.profile()&&state.profile!==this.profile()){this.clear('Profile changed. Refreshing coaching.');return false;}
  const key=state.profile+'\n'+state.generation,scope=key+'\n'+state.viewer;
  if(this.retired.has(key))return false;
  if(this.serviceKey!==null&&this.serviceKey!==key){this.retired.add(this.serviceKey);if(this.retired.size>16)this.retired.delete(this.retired.values().next().value);this.clear('');}
  if(this.scope!==null&&this.scope!==scope)this.clear('');
  if(this.scope===scope&&this.state&&state.revision<this.state.revision)return false;
  if(state._clientSerial<this.adoptedSerial)return false;
  if(state.history&&( !Number.isSafeInteger(state.history.revision)||!Array.isArray(state.history.entries)||state.history.entries.length>32))throw new Error('Private training history is unavailable.');
  if(state.history_error)state={...state,history:null,history_suggestion:null};
  else if(state.history&&state.history.revision<this.historyFloor)state={...state,history:this.state?.history||null,history_suggestion:this.state?.history_suggestion||null};
  else if(state.history)this.historyFloor=state.history.revision;
  else if(this.scope===scope&&this.state?.history)state={...state,history:this.state.history,history_suggestion:this.state.history_suggestion};
  this.adoptedSerial=state._clientSerial;this.serviceKey=key;this.scope=scope;this.state=state;this.loadError=false;this.q('error').textContent=this.actionError;this.feature(true);this.render();return true;
 }
 fail(error,action=false){
  if(disabled(error)){this.clear('');this.feature(false);}
  else if(error.status===401||error.status===403||['identity_changed','profile_changed','forbidden','feature_disabled','unauthorized'].includes(error.code))this.clear(error.message);
  else{
   const messages={conflict:'Training changed. Reload the current values before applying your draft.',busy:'A training change is still being applied.',reference_pending:'Reference data is being prepared. Try again shortly.',incompatible_reference:'Choose a comparable reference for this car and track.',reference_unavailable:'The selected reference is no longer available.'};
   this.q('error').textContent=messages[error.code]||error.message||'Coaching is unavailable.';
   if(action&&error.code&&error.status<500)this.actionError=this.q('error').textContent;
   // A transient failure (no code or 5xx) only reports the error: the last good
   // state and its controls stay, so the buttons do not flicker between polls. A failed
   // load or poll (not a rejected action) turns the badge to Unavailable, like the
   // engineer section, so it never reads Connecting next to an error.
   if(!action)this.loadError=true;
   this.q('reload').hidden=error.code!=='conflict';this.render();
  }
 }
 async poll(){
  if(this.dead||this.polling)return;this.polling=true;const generation=this.generation;const profile=this.profile(),duringWrite=this.sending;
  // A GET started inside a write can contain older facts with a newer request
  // serial. Let the command acknowledgement establish state, then poll afresh.
  try{const state=await this.api();if(!duringWrite)this.adopt(state,generation,profile);}
  catch(error){if(!this.dead&&generation===this.generation)this.fail(error);}
  finally{if(generation===this.generation)this.polling=false;if(!this.dead)this.schedule(this.state?1000:3000);}
 }
 async send(action,options){
  if(this.dead||!this.state||this.sending&&action!=='stop'||this.stopping)return;
  if(action==='stop'){this.generation++;this.abort();this.polling=false;this.stopping=true;}
  const generation=this.generation,editVersion=this.editVersion,profile=this.profile();
  const base=['start','objective','skill','reference'].includes(action)?this.base||this.state:this.state;
  if(action==='delete_history'&&!this.state.history){this.sending=false;return;}
  const body={expected:action==='delete_history'?this.state.history.revision:base.revision,action,options};if(this.admin)body.expectedProfile=base.profile;
  this.sending=true;this.actionError='';this.q('error').textContent='';this.q('reload').hidden=true;this.render();
  try{
   const state=await this.api(body);
   if(this.dead||generation!==this.generation)return;
   if(['start','stop','objective','skill','reference'].includes(action)&&editVersion===this.editVersion){this.dirty=false;this.base=null;}
   else if(this.dirty)this.base=state;
   this.adopt(state,generation,profile);
  }catch(error){if(!this.dead&&generation===this.generation)this.fail(error,true);}
  finally{if(generation===this.generation){this.sending=false;this.stopping=false;this.render();}if(!this.dead)this.schedule(0);}
 }
 render(){
  const s=this.state,c=s?.calibration||{},f=s?.focus||{},focus=f.focus,latest=s?.latest;const active=!!s?.active,pending=!!s?.pending;
  const phases={inactive:'Not training',stopped:'Stopped',pending:'Updating',calibrating:'Calibrating',provisional:'Provisional',ready:'Ready',paused:'Waiting for live evidence',unavailable:'Unavailable',forbidden:'Access changed'};
  this.q('phase').textContent=this.off?'Off':this.stopping?'Stopping':this.loadError?'Unavailable':pending?'Updating':phases[s?.phase]||(!s&&this.q('error').textContent?'Unavailable':'Connecting');
  this.q('start').disabled=!s||active||pending||this.sending;this.q('stop').disabled=!active||this.stopping;
  this.q('goal').disabled=!active||pending||this.sending;for(const id of ['set-skill','use-reference'])this.q(id).disabled=!active||pending||this.sending;
  for(const id of ['skip','keep'])this.q(id).disabled=!focus||pending||this.sending;
  this.q('show-evidence').disabled=!latest&&!focus;
  this.q('mode').disabled=!s||active;this.q('ai').disabled=!s||active;this.q('objective').disabled=!s;this.q('skill').disabled=!active;this.q('reference-choice').disabled=!active;
  this.q('calibration').textContent=c.phase?'Calibration: '+c.phase+' · '+(c.calibration_ids?.length||0)+' suitable laps · objective '+c.objective:active?'Waiting for fresh comparable completed laps.':'';
  const reference=c.reference,baseline=c.baseline;
  this.q('reference').textContent=(baseline?'Original baseline '+val(baseline.time_s,3)+' s. ':'')+(reference?(reference.source==='selected'?'Selected':'Personal')+' reference · lap '+reference.lap+' · '+val(reference.time_s,3)+' s.':'');
  this.q('label').textContent=focus?(focus.reviewed?'Reviewed passage':'Provisional exercise')+' · '+focus.label:'';
  this.q('focus').textContent=focus?.action||(active?'A focus will be chosen from suitable personal evidence.':'Start training to collect a fresh personal baseline.');
  this.q('progress').textContent=f.progress?.text||'';this.q('review').textContent=latest?[latest.observation,latest.check,latest.explanation?'AI interpretation: '+latest.explanation:null].filter(Boolean).join(' '):'';
  const evidence=this.q('evidence');evidence.replaceChildren();
  const attempt=f.attempts?.[f.attempts.length-1],check=attempt?.check||{},original=f.baseline?.passage||{};
  if(latest||focus){for(const [label,value] of [['Baseline lap IDs',(f.baseline?.lap_ids||[]).join(', ')||'Unavailable'],['Working reference ID',reference?.id||'Unavailable'],...(latest?[['Reviewed lap ID',latest.lap_id]]:[]),['Observation',latest?.observation||'Unavailable'],['Interpretation',latest?.interpretation||'Unavailable'],['Action',latest?.action||'No new technique score'],['Check',latest?.check||'Wait for a suitable lap'],['Original lap baseline · seconds',val(f.baseline?.time_s,3)],['Starting lap variation · seconds',val(f.baseline?.range_s,3)],['Comparable attempts',String(f.progress?.sample_count||0)],...(focus?.section_id?[['Original passage · seconds',val(original.time_s,3)],['Current passage · seconds',val(check.own_s,3)],['Following section · seconds',val(check.own_downstream_s,3)],['Passage exit · km/h',val(check.exit_speed_kmh)]]:[]),['Recent timing range · seconds',val(f.progress?.measure_range_s,3)]]){const row=el('tr');row.append(el('th',label),el('td',value));evidence.append(row);}}
  this.renderHistory();
  if(!this.dirty&&!pending){
   this.q('mode').value=c.mode||'practice';this.q('objective').value=c.objective||'balanced';this.q('ai').checked=!!s?.allow_ai;this.q('skill').value=f.skill||'';
   const chooser=this.q('reference-choice');chooser.replaceChildren(new Option('Automatic personal reference',''));for(const r of (s?.references||[]).slice(0,32))chooser.append(new Option(r.label,r.id));
   if(reference?.source==='selected'){if(!Array.from(chooser.options).some(o=>o.value===reference.id))chooser.append(new Option('Selected reference · lap '+reference.lap,reference.id));chooser.value=reference.id;}
  }
 }
 renderHistory(){
  const state=this.state,history=state?.history,rows=history?.entries||[],box=this.q('history-entries');
  const opened=new Set(Array.from(box.querySelectorAll('details[open]')).map(node=>node.dataset.historyId));box.replaceChildren();
  this.q('history-error').textContent=state?.history_error?'Private history is unavailable. Current coaching continues.':history?(rows.length?'Historical checkpoints remain separate from the current calibration.':'No private training summaries.'):'';
  this.q('history-delete-all').disabled=!rows.length||this.sending;
  const ended=rows.find(row=>row.status!=='active'),debrief=ended?.debrief;
  this.q('debrief').textContent=debrief?'Last debrief · car '+(ended.context?.car_id||'unknown')+' · '+(ended.context?.track_id||'unknown track')+': '+[debrief.worked,debrief.observed,debrief.interpretation,debrief.next].join(' '):'';
  const hint=state?.history_suggestion;this.q('history-suggestion').textContent=hint?.text||'';this.q('apply-history').hidden=!hint;this.q('apply-history').disabled=!state?.active||state?.pending||this.sending;
  const statuses={active:'Saved checkpoint',stopped:'Finished',interrupted:'Partial',invalidated:'Evidence reset',restarted:'Restarted',revoked:'Access changed'};
  for(const row of rows){
   const item=el('details');item.dataset.historyId=row.id;item.open=opened.has(row.id);
   item.append(el('summary',new Date(row.updated_at*1000).toLocaleString()+' · car '+(row.context?.car_id||'unknown')+' · '+(row.context?.track_id||'unknown track')+' · '+(statuses[row.status]||'Unavailable')));
   for(const [label,text] of [['Worked focus',row.debrief?.worked],['Observed change',row.debrief?.observed],['Interpretation',row.debrief?.interpretation],['Next exercise',row.debrief?.next]])item.append(el('p',label+': '+(text||'Unavailable')));
   item.append(el('p','Evidence: '+row.evidence_status+'. Baseline laps: '+(row.baseline?.lap_ids||[]).join(', '),'muted'));
   const button=el('button','Delete this summary');button.type='button';button.disabled=this.sending;button.onclick=()=>this.send('delete_history',{id:row.id});item.append(button);box.append(item);
  }
 }
 destroy(){this.dead=true;this.generation++;clearTimeout(this.timer);this.abort();this.state=null;this.base=null;this.retired.clear();this.host.replaceChildren();}
}

window.DriverRadio={mount:(host,options)=>new View(host,options),mountAdmin:host=>new View(host,{admin:true})};
})();

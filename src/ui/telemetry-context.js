/* Recording-owned context, optional preparation and profile templates. */
const tc = {open:false, target:null, doc:null, data:null, draft:{}, reply:null, session:'1',
  serial:0, dirty:false, pending:null, timer:null, generation:0, position:null, backups:new Map(), loading:false};
const TC_FIELDS = [
 ['title','Race / test name','text'], ['laps','Race laps','integer'], ['duration_min','Race duration (min)','number'],
 ['start_type','Start',[['','Unknown'],['rolling','Rolling'],['standing','Standing']]],
 ['bop','BoP','boolean'], ['fixed_setup','Fixed setup','boolean'],
 ['starting_fuel_fixed','Fixed starting fuel','boolean'], ['starting_fuel_l','Starting fuel (L)','number'],
 ['pit_stops','Required stops','integer'], ['pit_rule','Stop rule',[['','Unknown'],['minimum','At least'],['exact','Exactly'],['maximum','At most']]],
 ['required_tyres','Required tyres (codes, comma separated)','tyres'], ['pit_window','Pit window / extra rules','text'],
 ['tyre_x','Tyre wear multiplier','number'], ['fuel_x','Fuel use multiplier','number'],
 ['refuel_lps','Refuel rate (L/s)','number'], ['weather','Weather description','text'],
 ['time_of_day','Time of day','text'], ['time_progression','Time progression multiplier','number'],
 ['test_goal','Test objective','text'], ['conditions_note','Conditions / assumptions','text']
];
const TC_TYRES = ['', 'CH','CM','CS','SH','SM','SS','RH','RM','RS','IM','W','D','S'];
function tcEl(tag, text, cls) {
 const e=document.createElement(tag); if(text!=null)e.textContent=text; if(cls)e.className=cls; return e;
}
function tcUID() { return 'n'+Date.now().toString(36)+Math.random().toString(36).slice(2,12); }
function tcClone(v) { return JSON.parse(JSON.stringify(v)); }
function tcStatus(text, error=false) { $('tc-status').textContent=text; $('tc-status').className=error?'enverr':'sub'; }
function tcSession() {
 return tc.data.sessions[tc.session] ||= {settings:{},confirmed:false,stints:[],lap_roles:{}};
}
function tcLocalKey() { return tc.doc ? 'racecast-context-draft:'+String(tc.reply.profile)+':'+tc.doc.source_id+':'+String(tc.target||'prepared').replace(/\.part$/, '') : null; }
function tcKeepDraft() {
 if(!tc.doc)return;
 const backup=tcClone({revision:tc.doc.revision,data:tc.data,draft:tc.draft});tc.backups.set(tcLocalKey(),backup);
 try { localStorage.setItem(tcLocalKey(),JSON.stringify(backup)); } catch(_e) { /* The in-window backup remains available. */ }
}
function tcBackup() { let backup=tc.backups.get(tcLocalKey());if(!backup)try{backup=JSON.parse(localStorage.getItem(tcLocalKey()));}catch(_e){}return backup; }
function tcChanged() {
 tc.serial++;tc.dirty=true;tcKeepDraft();tcStatus('Saving…');clearTimeout(tc.timer);
 tc.timer=setTimeout(()=>tcSave(),650);
}
function tcField(parent, label, value, type, setter, key) {
 const wrap=tcEl('label',null,'tcfield');wrap.append(tcEl('span',label));let input;
 if(type==='boolean')type=[['','Unknown'],['true','Yes'],['false','No']];
 if(Array.isArray(type)) {
  input=tcEl('select');for(const [v,t] of type){const o=tcEl('option',t);o.value=v;input.append(o);}
  input.value=value==null?'':String(value);
 } else {input=tcEl('input');input.type='text';input.value=tc.draft[key]??(value==null?'':Array.isArray(value)?value.join(', '):String(value));
  if(['number','integer','required-integer'].includes(type))input.inputMode='decimal';}
 input.setAttribute('aria-label',label);if(key in tc.draft)input.classList.add('tcinvalid');
 input.oninput=()=>{
  const raw=input.value;let parsed=raw,valid=true;
  if(Array.isArray(type)){parsed=raw===''?null:raw==='true'?true:raw==='false'?false:raw;}
  else if(['number','integer','required-integer'].includes(type)) {parsed=raw.trim()===''?null:Number(raw);valid=parsed===null?type!=='required-integer':Number.isFinite(parsed)&&parsed>=0&&(type==='number'||Number.isInteger(parsed));}
  else if(type==='tyres'){parsed=raw.split(',').map(v=>v.trim().toUpperCase()).filter(Boolean);valid=parsed.every(v=>TC_TYRES.includes(v));}
  if(valid){delete tc.draft[key];setter(parsed);input.classList.remove('tcinvalid');}
  else {tc.draft[key]=raw;input.classList.add('tcinvalid');}
  tcChanged();
 };
 wrap.append(input);parent.append(wrap);return input;
}
async function tcOpen(prepared=false) {
 tc.open=true;tc.returnFocus=document.activeElement;tc.position=!prepared&&tmState.lapB&&tmState.noteCursorKey===tmKey(tmState.lapB)&&tmState.noteCursor?tcClone(tmState.noteCursor):null;
 $('tm-context-modal').hidden=false;
 await tcLoad(prepared?null:tmState.rec);$('tc-target').focus();
}
async function tcClose() {
 await tcSave();tc.open=false;tc.generation++;tcReleaseLoad();$('tm-context-modal').hidden=true;tc.returnFocus?.focus?.();
}
function tcReleaseLoad() {
 tc.loading=false;
 for(const [control,disabled] of tc.loadControls||[])control.disabled=disabled;
 tc.loadControls=null;
}
async function tcLoad(rec, before=null) {
 tcReleaseLoad();
 const gen=++tc.generation;tc.loading=true;tcStatus('Loading context…');
 const controls=[...($('tm-context-modal').querySelectorAll?.('button,input,select,textarea')||[])].filter(e=>e.id!=='tc-close');
 const disabled=controls.map(e=>e.disabled);controls.forEach(e=>e.disabled=true);
 tc.loadControls=controls.map((e,i)=>[e,disabled[i]]);
 const q=new URLSearchParams();if(rec)q.set('rec',rec);if(before!=null)q.set('before',before);
 let d;try{d=await tmGet('/api/telemetry/context?'+q);}catch(_e){d={ok:false,error:'Context unavailable'};}
 finally{if(gen===tc.generation)tcReleaseLoad();}
 if(gen!==tc.generation||!tc.open)return;
 if(!d.ok||!d.context){$('tc-target').value=tc.target||'';tcStatus(d.error||'Context unavailable',true);return;}
 tc.target=rec||null;if(tc.target!==tmState.rec)tc.position=null;
 tc.reply=d;tc.doc=d.context;tc.data=tcClone(tc.doc.data);tc.draft=tcClone(tc.doc.draft||{});tc.dirty=false;
 tc.session=d.sessions.includes(tc.session)?tc.session:d.sessions[0]||'1';
 const backup=tcBackup();
 $('tc-local-draft').hidden=!backup;
 if(backup&&backup.revision===tc.doc.revision){tc.data=backup.data;tc.draft=backup.draft||{};tc.dirty=true;}
 tcRender();tcStatus(d.recording_active?'Recording continues; notes remain editable':
   Object.keys(tc.draft).length?'Saved values; unfinished inputs retained as draft':'Context loaded; edits save automatically');
 if(backup&&backup.revision!==tc.doc.revision)tcStatus('Saved context changed. Your local draft is retained; choose whether to restore it.',true);
 if(backup&&backup.revision===tc.doc.revision)tc.timer=setTimeout(()=>tcSave(),650);
}
async function tcSelectTarget(value) {
 if(!await tcSave())return;
 await tcLoad(value||null);
}
async function tcSave() {
 clearTimeout(tc.timer);
 if(tc.loading)return false;
 if(tc.pending){const ok=await tc.pending;if(!ok)return false;if(tc.dirty)return tcSave();return true;}
 if(!tc.doc||!tc.dirty)return true;
 const gen=tc.generation,serial=tc.serial,target=tc.target;
 const payload={rec:target,profile:tc.reply.profile,source_id:tc.doc.source_id,
  expected_revision:tc.doc.revision,action:'save',data:tcClone(tc.data),draft:tcClone(tc.draft)};
 tc.pending=(async()=>{
  let d;try{const r=await fetch('/api/telemetry/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});d=await r.json();}
  catch(_e){tcStatus('Could not save. Your draft is retained locally.',true);return false;}
  if(gen!==tc.generation)return d.ok;
  if(!d.ok){tcStatus(d.error||'Could not save context',true);return false;}
  const changed=d.context.revision!==tc.doc.revision;tc.doc=d.context;tc.reply=d;
  if(serial===tc.serial){tc.dirty=false;tc.backups.delete(tcLocalKey());try{localStorage.removeItem(tcLocalKey());}catch(_e){}}
  else {tcKeepDraft();tc.timer=setTimeout(()=>tcSave(),400);}
  tcHistory();tcStatus(Object.keys(tc.draft).length?'Saved valid values; unfinished inputs retained as draft':
    'Saved · revision '+tc.doc.revision);
  if(changed&&target===tmState.rec)await tmRefreshContext(target);
  return true;
 })();
 const ok=await tc.pending;tc.pending=null;return ok;
}
async function tcAction(action, extra={}) {
 const gen=tc.generation,target=tc.target;
 if(!await tcSave()||!tc.doc||gen!==tc.generation||target!==tc.target)return;
 if((action==='restore'&&!Number.isInteger(extra.revision))||(['apply-template','delete-template'].includes(action)&&!extra.template_id))return;
 const payload={rec:tc.target,profile:tc.reply.profile,source_id:tc.doc.source_id,
  expected_revision:tc.doc.revision,action,...extra};
 const controls=[...$('tm-context-modal').querySelectorAll('button,input,select,textarea')].filter(e=>e.id!=='tc-close');
 const disabled=controls.map(e=>e.disabled);controls.forEach(e=>e.disabled=true);
 let d;try{const r=await fetch('/api/telemetry/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});d=await r.json();}
 catch(_e){tcStatus('Could not apply the change',true);return;}
 finally{controls.forEach((e,i)=>e.disabled=disabled[i]);}
 if(gen!==tc.generation||target!==tc.target)return;
 if(!d.ok){tcStatus(d.error||'Could not apply the change',true);return;}
 tc.reply=d;tc.doc=d.context;tc.data=tcClone(d.context.data);tc.draft=tcClone(d.context.draft||{});
 tcRender();tcStatus('Saved · revision '+tc.doc.revision);
 if(tc.target===tmState.rec)await tmRefreshContext(tc.target);
}
function tcRestoreLocal() {
 const backup=tcBackup();
 if(!backup)return;tc.data=backup.data;tc.draft=backup.draft||{};tcRender();tcChanged();
}
function tcRender() {
 const targets=$('tc-target');targets.textContent='';
 const prepared=tcEl('option','Prepare next recording');prepared.value='';targets.append(prepared);
 for(const r of tmState.recs){const o=tcEl('option',r.rec+(r.recording?' · recording':''));o.value=r.rec;targets.append(o);}
 targets.value=tc.target||'';
 const sessions=$('tc-session');sessions.textContent='';
 for(const n of [...new Set([...tc.reply.sessions,...Object.keys(tc.data.sessions)])].sort((a,b)=>Number(a)-Number(b))){const o=tcEl('option','Session '+n);o.value=n;sessions.append(o);}
 sessions.value=tc.session;
 const s=tcSession();s.settings||={};s.stints||=[];s.lap_roles||={};
 const settings=$('tc-settings');settings.textContent='';
 for(const [key,label,type] of TC_FIELDS)tcField(settings,label,s.settings[key],type,v=>s.settings[key]=v,'settings.'+tc.session+'.'+key);
 $('tc-confirmed').checked=!!s.confirmed;
 const templates=$('tc-template');templates.textContent='';const none=tcEl('option','Choose settings template');none.value='';templates.append(none);
 for(const [key,t] of Object.entries(tc.reply.templates||{})){const o=tcEl('option',t.name);o.value=key;templates.append(o);}
 tcStints();tcNotes();tcRoles();tcHistory();
 $('tc-sources').textContent=tc.reply.recording_active?'The capture is active. Analysis becomes available after recording stops.':
  tc.target?'Manual context supplements measurements. Missing values remain unknown.':'Preparation is attached once to the next recording; later GT7 sessions need a new confirmation.';
 const warnings=[tc.reply.templates_error?'Templates unavailable: '+tc.reply.templates_error:'',
                 tc.reply.history_error?'History unavailable: '+tc.reply.history_error:''].filter(Boolean);
 if(warnings.length)$('tc-sources').textContent+=' '+warnings.join(' · ');
}
function tcAddSession() {
 const n=Math.max(0,...Object.keys(tc.data.sessions).map(Number),...tc.reply.sessions.map(Number))+1;
 tc.data.sessions[String(n)]={settings:{},confirmed:false,stints:[],lap_roles:{}};tc.session=String(n);tcRender();tcChanged();
}
function tcSaveTemplate() {
 const name=$('tc-template-name').value.trim();if(!name){tcStatus('Enter a template name',true);return;}
 tcAction('save-template',{template_id:tcUID(),name,settings:tcSession().settings});
}
function tcHistory() {
 const el=$('tc-history');el.textContent='';const none=tcEl('option','Choose an earlier whole-context revision');none.value='';el.append(none);
 for(const h of tc.reply.history||[]){const text='Revision '+h.revision+(h.updated_at!=null?' · '+new Date(h.updated_at*1000).toLocaleString():'');const o=tcEl('option',text);o.value=String(h.revision);el.append(o);}
}
function tcButton(parent,text,fn,cls='') {const b=tcEl('button',text,cls);b.type='button';b.onclick=fn;parent.append(b);return b;}
function tcStints() {
 const root=$('tc-stints');root.textContent='';const s=tcSession();
 s.stints.forEach((stint,i)=>{
  const card=tcEl('section',null,'tcrow');const head=tcEl('div',null,'viewhead');head.append(tcEl('b','Stint '+(i+1)));
  tcButton(head,'Remove',()=>{s.stints.splice(i,1);tcStints();tcChanged();});card.append(head);
  const grid=tcEl('div',null,'tcgrid');card.append(grid);const key='stint.'+stint.id+'.';
  tcField(grid,i?'Service occurs in lap':'Initial stint starts in lap',stint.start_lap,'required-integer',v=>stint.start_lap=v,key+'lap');
  tcField(grid,'Tyre compound',stint.compound,TC_TYRES.map(v=>[v,v||'Unknown']),v=>stint.compound=v,key+'compound');
  tcField(grid,'Assignment confirmed',stint.confirmed,'boolean',v=>stint.confirmed=v,key+'confirmed');
  tcField(grid,'Tyres changed',stint.tyre_service,'boolean',v=>stint.tyre_service=v,key+'service');
  tcField(grid,'Refuelled during service',stint.refuel,'boolean',v=>stint.refuel=v,key+'refuel');
  tcField(grid,'Manually reported refuel amount (L)',stint.refuel_l,'number',v=>stint.refuel_l=v,key+'refuel_l');
  tcField(grid,'Complete warmup laps after change',stint.warmup_laps,'required-integer',v=>stint.warmup_laps=v,key+'warmup');
  const service=tcEl('select');const unset=tcEl('option','Service time unknown');unset.value='';service.append(unset);
  for(const hint of tc.reply.service_hints||[]){if(String(hint.session)!==tc.session)continue;
   for(const event of hint.events||[]){const label='Lap '+hint.lap+' · possible '+event.kind.replaceAll('_',' ')+' · '+event.t_s.toFixed(3)+' s into recording';
    const o=tcEl('option',label);o.value=JSON.stringify({lap:hint.lap,t:event.t_s});service.append(o);}}
  for(const option of service.options){if(option.value&&JSON.parse(option.value).t===stint.start_recording_s)service.value=option.value;}
  const wrap=tcEl('label',null,'tcfield');wrap.append(tcEl('span','Use a proposed service time'),service);grid.append(wrap);
  service.onchange=()=>{if(service.value){const e=JSON.parse(service.value);stint.start_lap=e.lap;stint.start_recording_s=e.t;}else stint.start_recording_s=null;tcStints();tcChanged();};
  tcField(grid,'Exact service time (seconds into recording, optional)',stint.start_recording_s,'number',v=>stint.start_recording_s=v,key+'time');
  stint.strategy||={};tcStrategy(card,stint.strategy,key+'strategy.');
  tcField(grid,'Stint observations',stint.note,'text',v=>stint.note=v,key+'note');
  const changes=tcEl('div',null,'tcchanges');card.append(changes);
  (stint.strategy_changes||[]).forEach((change,j)=>{const row=tcEl('section',null,'tcrow');row.append(tcEl('b','Strategy change '+(j+1)));
   tcField(row,'From lap',change.lap,'required-integer',v=>change.lap=v,key+'change.'+j+'.lap');
   tcField(row,'From recording time (optional)',change.recording_s,'number',v=>change.recording_s=v,key+'change.'+j+'.time');
   change.strategy||={};tcStrategy(row,change.strategy,key+'change.'+j+'.strategy.');
   tcButton(row,'Remove change',()=>{stint.strategy_changes.splice(j,1);tcStints();tcChanged();});changes.append(row);});
  tcButton(card,'Add strategy change',()=>tcAddStrategyChange(stint));
  root.append(card);
 });
}
function tcAddStrategyChange(stint) {
 (stint.strategy_changes||=[]).push({lap:stint.start_lap,strategy:tcClone(stint.strategy||{})});
 tcStints();tcChanged();
}
function tcStrategy(parent,strategy,key) {
 const grid=tcEl('div',null,'tcgrid');parent.append(grid);
 tcField(grid,'Fuel map',strategy.fuel_map,[['','Unknown'],...[1,2,3,4,5,6].map(v=>[String(v),String(v)])],v=>strategy.fuel_map=v==null?null:Number(v),key+'map');
 tcField(grid,'Intentional shortshifting',strategy.shortshift,'boolean',v=>strategy.shortshift=v,key+'shortshift');
 tcField(grid,'Strategy note',strategy.note,'text',v=>strategy.note=v,key+'note');
 const targets=tcEl('details');targets.append(tcEl('summary','Per-gear saving targets (RPM, optional)'));const fields=tcEl('div',null,'tcgrid');targets.append(fields);parent.append(targets);
 for(let gear=1;gear<=8;gear++)tcField(fields,'Gear '+gear+' → '+(gear+1),strategy.targets?.[String(gear)],'number',v=>{strategy.targets||={};if(v==null)delete strategy.targets[String(gear)];else strategy.targets[String(gear)]=v;},key+'target.'+gear);
}
function tcAddStint() {
 const s=tcSession();s.stints.push({id:tcUID(),start_lap:s.stints.length?((tmState.lapB?.lap)||1):1,
  compound:null,confirmed:false,tyre_service:s.stints.length?null:false,warmup_laps:s.stints.length?1:0,strategy:{}});
 tcStints();tcChanged();
}
function tcNotes() {
 const root=$('tc-notes');root.textContent='';
 (tc.data.notes||[]).forEach((note,i)=>{
  if(note.scope!=='recording'&&String(note.session)!==tc.session)return;
  const card=tcEl('section',null,'tcrow');const grid=tcEl('div',null,'tcgrid');card.append(grid);
  tcField(grid,'Note applies to',note.scope,[['recording','Whole recording'],['session','This GT7 session'],['lap','A lap']],v=>{note.scope=v;if(v!=='recording')note.session=Number(tc.session);if(v==='lap')note.lap??=1;else delete note.position;tcNotes();},'note.'+note.id+'.scope');
  if(note.scope==='lap')tcField(grid,'Lap',note.lap,'required-integer',v=>{note.lap=v;delete note.position;},'note.'+note.id+'.lap');
  const text=tcEl('textarea');text.rows=3;text.value=note.text;text.setAttribute('aria-label','Note text');text.oninput=()=>{note.text=text.value;tcChanged();};card.append(text);
  if(note.position)card.append(tcEl('p','Track position attached · x '+note.position.x.toFixed(1)+' / z '+note.position.z.toFixed(1),'sub'));
  if(tc.position&&note.scope==='lap'&&tc.target===tmState.rec&&note.session===tmState.lapB?.session&&note.lap===tmState.lapB?.lap)tcButton(card,'Attach current chart position',()=>{note.position=tcClone(tc.position);tcNotes();tcChanged();});
  tcButton(card,'Remove note',()=>{tc.data.notes.splice(i,1);tcNotes();tcChanged();});root.append(card);
 });
}
function tcAddNote() {
 const here=tc.target===tmState.rec&&tmState.lapB&&Number(tc.session)===tmState.lapB.session;
 tc.data.notes||=[];tc.data.notes.push({id:tcUID(),scope:here?'lap':tc.target?'session':'recording',
  session:Number(tc.session),lap:here?tmState.lapB.lap:1,text:''});tcNotes();tcChanged();
}
function tcRoles() {
 const root=$('tc-roles');root.textContent='';const s=tcSession();
 for(const [lap,role] of Object.entries(s.lap_roles||{})){
  const row=tcEl('div',null,'tcgrid');tcField(row,'Lap '+lap+' role',role,
   ['unknown','regular','first','pit','warmup'].map(v=>[v,v==='unknown'?'Use inferred role':v]),v=>s.lap_roles[lap]=v,'role.'+lap);
  tcButton(row,'Remove override',()=>{delete s.lap_roles[lap];tcRoles();tcChanged();});root.append(row);
 }
}
function tcAddRole() {
 const lap=$('tc-role-lap').value.trim();if(!/^\d+$/.test(lap)){tcStatus('Enter a lap number',true);return;}
 tcSession().lap_roles[lap]='unknown';tcRoles();tcChanged();
}
if(typeof window!=='undefined'&&window.addEventListener)window.addEventListener('beforeunload',e=>{
 if(tc.dirty){tcKeepDraft();e.preventDefault();e.returnValue='';}
});

if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('keydown',e=>{
 if(!tc.open)return;
 if(e.key==='Escape'){e.preventDefault();tcClose();}
 if(e.key==='Tab'){const fields=[...$('tm-context-modal').querySelectorAll('button,input,select,textarea')].filter(f=>!f.disabled&&f.offsetParent!==null);
  if(fields.length&&e.shiftKey&&document.activeElement===fields[0]){e.preventDefault();fields.at(-1).focus();}
  else if(fields.length&&!e.shiftKey&&document.activeElement===fields.at(-1)){e.preventDefault();fields[0].focus();}}
});

async function tcOlderHistory(){if(!await tcSave())return;const h=tc.reply?.history||[];if(h.length)tcLoad(tc.target,Math.min(...h.map(r=>r.revision)));}
function tcRestoreHistory(){const value=$('tc-history').value;if(value!=='')tcAction('restore',{revision:Number(value)});}

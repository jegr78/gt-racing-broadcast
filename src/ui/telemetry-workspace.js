// Local archive analysis: recordings -> laps -> reference A / analysed lap B.
// Values from recordings reach the page only through textContent and SVG attributes.
const TM_NS = 'http://www.w3.org/2000/svg';
const tmState = {included: null, available: [], anchor: null, loaded: false, recs: [], rec: null, recLaps: [], pool: null,
                 a: null, b: null, lapA: null, lapB: null, sectorM: 200, tracks: null,
                 chart: null, map: null, cursor: null, positionD:0, positionKey:null, profile: null, summary: null,
                 recTrack: null, sessionTracks: null, trackSession: null};
const tmLapCache = new Map();
const TM_STEP = 5;                        // gt7_laps.STEP_M: one trace point per 5 m
const TM_PAD = {l: 104, r: 10, t: 4, gap: 10, b: 22};
const TM_CH = [
  {key: 'delta', label: 'Delta B-A', unit: 's', h: 100, sym: true},
  {key: 'speed_kmh', label: 'Speed', unit: 'km/h', h: 130, lo: 0},
  {key: 'throttle', label: 'Throttle', unit: '%', h: 72, lo: 0, hi: 100},
  {key: 'brake', label: 'Brake', unit: '%', h: 72, lo: 0, hi: 100},
  {key: 'steer_deg', label: 'Steering', unit: 'deg', h: 80, sym: true},
  {key: 'gear', label: 'Gear', unit: '', h: 60, lo: 0, step: true},
];
// The values a panel labels on its left edge, top to bottom: a fixed scale its ends, a
// symmetric one its largest swing and zero, any other the measured min and max.
function tmTicks(c, vmin, vmax) {
  if (!(vmin <= vmax)) return [];
  if (c.hi != null) return [c.hi, c.lo];
  if (c.sym) { const m = Math.max(-vmin, vmax); return m > 0 ? [m, 0, -m] : [0]; }
  return vmax > vmin ? [vmax, vmin] : [vmax];
}
function tmTickText(c, v) {
  if (c.key !== 'delta') return String(Math.round(v));
  return v === 0 ? '0' : (v > 0 ? '+' : '') + v.toFixed(2);
}
const tmLapsReq = new Map();     // url -> pending request, so a cold index runs once
let tmLapGen = 0;                // bumped when cached laps go stale; older answers are not cached
const tmSeq = {load: 0, rec: 0, b: 0, pair: 0, track: 0};   // only the newest call of each kind renders

function tmKey(l) { return l.rec + '|' + l.session + '|' + l.lap; }
function tmTime(s) {
  if (!Number.isFinite(s) || s < 0) return '—';
  const ms = Math.round(s * 1000), m = Math.floor(ms / 60000), r = (ms % 60000) / 1000;
  return m + ':' + (r < 10 ? '0' : '') + r.toFixed(3);
}
function tmSigned(v) {
  if (v == null) return '—';
  if (Math.abs(v) < 0.0005) return '0.000';
  return (v > 0 ? '+' : '') + v.toFixed(3);
}
// Mini-sector colour for B minus A; under half a millisecond reads as even, like tmSigned.
function tmSecClass(diff) {
  if (diff == null || Math.abs(diff) < 0.0005) return 'even';
  return diff < 0 ? 'gain' : 'loss';
}
function tmEl(tag, attrs, parent) {
  const e = document.createElementNS(TM_NS, tag);
  Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
  if (parent) parent.appendChild(e);
  return e;
}
function tmErr(msg) { const e = $('tm-err'); e.textContent = msg?.message || msg || ''; e.hidden = !msg; }
async function tmGet(url) {
  try { return await (await fetch(url, {cache: 'no-store'})).json(); }
  catch (e) { return {ok: false, error: 'Local UI service not reachable.'}; }
}
function tmShared(url) {
  if (!tmLapsReq.has(url)) {
    const p = tmGet(url).finally(() => { if (tmLapsReq.get(url) === p) tmLapsReq.delete(url); });
    tmLapsReq.set(url, p);
  }
  return tmLapsReq.get(url);
}
function tmDropLaps() {
  tmLapGen++;
  tmIndexLines = [];
  tmLapCache.clear();
  tmLapsReq.clear();
}
// Builds the missing lap indexes in the telemetry-index job; pool requests share one run,
// and it runs once per lap generation and GT7 data version, so a recording that cannot be
// indexed does not start it on every lap B.
let tmIndexing = null;
let tmIndexedGen = -1;
let tmIndexedVer = null;           // the data_version of the answer that started this page's last run
let tmIndexLines = [];             // the output of that run, which names a recording it could not index
function tmNote(text) { $('tm-note').textContent = text; $('tm-note').hidden = false; }
// True when an unindexed answer `d` may start the job: a new lap generation, or GT7 data
// that changed since the last run, e.g. by the relay's background data update.
function tmMayIndex(d) {
  return tmIndexedGen !== tmLapGen || (d.data_version != null && d.data_version !== tmIndexedVer);
}
function tmIndexAll(ver) {
  if (!tmIndexing) {
    const gen = tmLapGen;
    const p = (async () => {
      let job = await startJob('telemetry-index');
      if (job.error && job.job_id) {
        if (gen === tmLapGen) tmNote('Indexing runs in another window, waiting for it…');
        job = {job_id: job.job_id, joined: true};
      }
      const r = job.error ? job : await jobResult(job.job_id, 20);
      if (r.error || gen !== tmLapGen) return r;
      tmIndexedGen = gen;
      if (!job.joined) tmIndexedVer = ver;          // a joined run's version is unknown
      tmIndexLines = r.lines || [];
      if (job.joined) $('tm-note').hidden = true;
      if (r.code !== 0) {
        const bad = (r.lines || []).filter(l => l.includes('not indexed'));
        tmNote('Some recordings could not be indexed: '
          + (bad.length ? bad : (r.lines || []).slice(-1)).join('; '));
      }
      tmRefreshRecs();
      return r;
    })().finally(() => { if (tmIndexing === p) tmIndexing = null; });
    tmIndexing = p;
  }
  return tmIndexing;
}
// `ask()` once more after the index job when the recording `rec` has no lap index yet. A
// recording the job of this generation could not index answers with the job's line as an
// error instead of starting it again. `busy(true)` and `busy(false)` bracket the job.
async function tmIndexed(rec, ask, stale, busy) {
  let d = await ask();
  for (let runs = 0; runs < 2 && d.ok && d.unindexed && tmMayIndex(d); runs++) {
    if (busy) busy(true);
    let job;
    try { job = await tmIndexAll(d.data_version); } finally { if (busy) busy(false); }
    if (stale()) return {ok: false, stale: true};
    if (job.error) return {ok: false, error: job.error};
    d = await ask();
  }
  if (!d.ok || !d.unindexed) return d;
  const last = tmIndexLines[tmIndexLines.length - 1] || '';
  const failed = /failed: (.*)$/.exec(last);
  const line = tmIndexLines.find(l => l.startsWith(rec + ': not indexed'))
    || (failed && failed[1].split(', ').includes(rec) ? last : '');
  return {ok: false, error: line || (d.note || rec + ' has no lap index yet')
    + '. Refresh runs the index job again.'};
}
async function tmRefreshRecs() {
  const seq = tmSeq.load;
  const d = await tmGet('/api/telemetry/recordings');
  if (seq !== tmSeq.load || !d.ok) return;
  tmState.recs = d.recordings || [];
  workspaceArchive();
  tmRenderRecs();
}
// GT7 names some tracks after their only layout ("Grand Valley - Highway 1" + "Highway 1"):
// a layout already ending the track name is not appended twice.
function tmTrackName(t) {
  const lay = t.layout || '', tl = t.track.toLowerCase(), ll = lay.toLowerCase();
  const dup = lay && (tl === ll || tl.endsWith(' ' + ll));   // a whole trailing name part only
  return t.track + (lay && !dup ? ' - ' + lay : '') + (t.reverse ? ' (reverse)' : '');
}

function tmTrackLabel(t) {
  if (!t) return 'track unknown';
  if (t.candidates) return 'track ambiguous';
  return tmTrackName(t);
}
function tmLapTrack(l) { return l.track ? tmTrackName({track: l.track, layout: l.layout}) : 'track unknown'; }

function tmPlaceholder(id, text) {
  const sel = $(id);
  sel.textContent = '';
  const o = document.createElement('option');
  o.value = ''; o.textContent = text; o.disabled = true; o.selected = true;
  sel.appendChild(o);
  sel.disabled = true;
}
function tmClearPair(status) {
  tmSeq.pair++;
  tmState.lapA = tmState.lapB = tmState.pool = tmState.a = null;
  tmState.chart = tmState.map = tmState.cursor = null;
  tmState.positionD=0;tmState.positionKey=null;
  ['tm-charts', 'tm-map', 'tm-sectors', 'tm-map-sub', 'tm-map-position', 'tm-map-numbering'].forEach(id => { $(id).textContent = ''; });
  $('tm-map-legend').hidden = $('tm-sec-legend').hidden = true;
  tmPlaceholder('tm-a', status ? 'loading…' : 'no lap');
  tmPlaceholder('tm-b', status ? 'loading…' : 'no lap');
  $('tm-sec-sub').textContent = status || '';
}
// The post-event report's figures for the recording (racecast `_telemetry_summary`), one
// block per track and car, with the lap-time trend; a trend dot picks that lap as B.
function tmRenderSummary(sum) {
  const box = $('tm-summary');
  tmState.summary = sum || null;
  $('workspace-session-summary').hidden = !sum;
  box.textContent = '';
  box.hidden = !(sum && sum.groups && sum.groups.length);
  if (box.hidden) return;
  const ordered = tmState.recLaps.slice().sort((x, y) => (x.start_t_s || 0) - (y.start_t_s || 0));
  sum.groups.forEach(g => {
    const grp = document.createElement('div');
    grp.className = 'tmgrp';
    const h = document.createElement('h3');
    h.textContent = [g.track, g.car, ...('context_confirmed' in g ?
      [g.compound || 'Tyres unknown', g.context_confirmed ? 'Context confirmed' : 'Context unconfirmed'] : [])].filter(Boolean).join(' · ');
    grp.appendChild(h);
    const tile = (value, label, title) => {
      if (value == null) return;
      const t = document.createElement('div');
      t.className = 'tmtile';
      if (title) t.title = title;
      const v = document.createElement('b'), l = document.createElement('span');
      v.textContent = value; l.textContent = label;
      t.append(v, l);
      grp.appendChild(t);
    };
    const bl = g.best_lap;
    tile(g.best_s != null ? tmTime(g.best_s) : null,
         'best lap' + (bl ? ' · lap ' + bl.lap + (bl.session > 1 ? ' S' + bl.session : '') : ''));
    const theory=g.sector_theory;
    tile(theory?.time_s!=null?tmTime(theory.time_s):null, theory?.confirmed?'sector best':'unconfirmed sector sum',
         'Ideal larger-sector composition; entry-speed dependencies remain');
    if(g.theoretical_s!=null){const diagnostic=document.createElement('details'),title=document.createElement('summary'),value=document.createElement('span');
      title.textContent='Mini-sector diagnostic sum';value.textContent=tmTime(g.theoretical_s)+' · fine-segment composition';diagnostic.append(title,value);grp.append(diagnostic);}
    tile(g.consistency_s != null ? '± ' + g.consistency_s.toFixed(3) + ' s' : null, 'consistency',
         'Standard deviation of the counted lap times');
    tile(g.fuel_per_lap_l != null ? g.fuel_per_lap_l.toFixed(2) + ' L' : null, 'fuel per lap');
    tile(g.laps_counted + ' of ' + g.laps_total, 'pace laps',
         'Laps with an eligible capture and valid comparison clock');
    tile(g.tyre_avg_c ? g.tyre_avg_c.map(v => v.toFixed(1)).join(' · ') + ' °C' : null,
         'tyres FL · FR · RL · RR', 'Average tyre temperature over the counted laps');
    tmTrend(grp, g.trend || [], ordered);
    box.appendChild(grp);
  });
}
// Lap times in driving order like the report: counted laps set the scale, the others are
// clamped into it and greyed, the best is green.
function tmTrend(parent, trend, ordered) {
  const pts = trend.filter(p => p.time_s != null && Number.isFinite(p.time_s));
  if (!pts.length) return;
  const svg = tmEl('svg', {class: 'tmtrend', role: 'img', 'aria-label': 'Lap time trend'}, parent);
  // Drawn at the box's own width, so dots and labels keep their size (a hidden view: 900).
  const W = Math.max(300, Math.round($('tm-summary').getBoundingClientRect().width) - 24 || 900);
  const H = 96, L = 70, R = 10, T = 8, Bm = 8;
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  const scale = pts.filter(p => p.counted).map(p => p.time_s);
  const ref = scale.length ? scale : pts.map(p => p.time_s);
  let lo = Math.min(...ref), hi = Math.max(...ref);
  const m = (hi - lo) * 0.1 || 1;
  lo -= m; hi += m;
  [[T, hi], [H - Bm, lo]].forEach(([y, v]) => {
    tmEl('line', {x1: L, x2: W - R, y1: y, y2: y}, svg);
    tmEl('text', {x: L - 10, y: y + 3.5, 'text-anchor': 'end'}, svg).textContent = tmTime(v);
  });
  const x0 = L + 8, step = (W - x0 - R) / Math.max(1, pts.length - 1);   // clear of the labels
  pts.forEach((p, i) => {
    const v = Math.min(hi, Math.max(lo, p.time_s));
    const c = tmEl('circle', {cx: pts.length > 1 ? x0 + i * step : (x0 + W - R) / 2,
      cy: T + (hi - v) / (hi - lo) * (H - T - Bm), r: p.best ? 5 : 3.5,
      class: 'tm-tr' + (p.best ? ' best' : p.counted ? '' : ' nc')}, svg);
    tmEl('title', {}, c).textContent = 'Lap ' + p.lap + ': ' + tmTime(p.time_s)
      + (p.counted ? '' : ' (not counted)');
    const lap = ordered[p.n - 1];
    if (lap) c.onclick = () => tmSelectB(lap);
  });
}

function tmClear(msg) {
  tmClearPair('');
  tmRenderSummary(null);
  tmState.b = null;
  $('tm-rec-track').textContent = '';
  $('tm-laps').textContent = msg || '';
  $('tm-settrack').hidden = true;
}
function tmReset() {
  tmReferenceLayoutsReset();
  Object.keys(tmSeq).forEach(k => { tmSeq[k]++; });
  tmState.included = null; tmState.available = []; tmState.anchor = null;
  tmState.loaded = false; tmState.rec = null; tmState.recs = []; tmState.recLaps = [];
  tmRenderRecordingMenu();
  tmState.tracks = null;
  tmDropLaps();
  tmIndexing = null;
  tmIndexedVer = null;
  $('tm-note').hidden = true;
  $('tm-recs').textContent = '';
  tmErr('');
  tmClear('');
}

async function tmLoad() {
  const seq = ++tmSeq.load;
  tmDropLaps();
  tmState.tracks = null;
  $('tm-note').hidden = true;
  tmErr('');
  const d = await tmGet('/api/telemetry/recordings');
  if (seq !== tmSeq.load) return;
  if (!d.ok) {
    tmState.loaded = false; tmState.recs = []; tmState.rec = null; tmState.recLaps = [];
    $('tm-recs').textContent = '';
    tmClear('');
    tmErr(d.error || 'could not list the recordings');
    return;
  }
  tmState.loaded = true;
  tmState.recs = d.recordings || [];
  workspaceArchive();
  const keep = tmState.recs.find(r => r.rec === tmState.rec && !r.recording);
  const first = keep || tmState.recs.find(r => !r.recording);
  if (!first) {
    tmState.rec = null;
    tmState.recLaps = [];
    tmRenderRecs();
    tmClear(tmState.recs.length
      ? 'The relay is still writing the only recording. Stop the recording to analyse it.'
      : 'No recordings yet. The Director Panel REC key or TELEMETRY_RECORD=1 in the profile records a session.');
    return;
  }
  await tmSelectRec(first.rec);
}

function tmRenderRecs() {
  workspaceArchive();
  const box = $('tm-recs');
  box.textContent = '';
  tmState.recs.forEach(r => {
    const b = document.createElement('button');
    b.className = 'tmitem' + (r.rec === tmState.rec ? ' sel' : '');
    const name = document.createElement('span');
    b.title = r.rec;
    name.textContent = tmRecordingLabel(r) + (r.recording ? ' (recording)' : r.partial ? ' (unclosed)' : '');
    name.style.gridColumn = '1 / -1';
    const sub = document.createElement('span');
    sub.className = 'tmsub';
    sub.textContent = (r.recording ? 'being written' : r.indexed ? tmTrackLabel(r.track) : 'not indexed yet')
      + ' · ' + Math.round(r.duration_s / 60) + ' min · ' + (r.size / 1e6).toFixed(1) + ' MB'
      + (r.partial_segments ? ' · ' + r.partial_segments + ' partial' : '');
    b.append(name, sub);
    if (r.recording) b.disabled = true;           // laps/lap/learn refuse the open file
    else b.onclick = () => tmSelectRec(r.rec);
    box.appendChild(b);
  });
  const sel = tmState.recs.find(r => r.rec === tmState.rec);

  $('tm-del').disabled = !sel || !!sel.recording;   // the relay's open file is never deleted
}

async function tmRunCommand(name,params,quiet=false) {
 const profile=tmState.profile;
 let job=await startJob(name,params);
 if(name==='telemetry-replay-status'&&job.error&&job.job_id)job={job_id:job.job_id};
 if(job.error){if(!quiet)tmErr(job.error);return null;}
 if(profile!==tmState.profile)return null;
 const result=await jobResult(job.job_id,30);
 if(profile!==tmState.profile)return null;
 if(result.error||result.code!==0){if(!quiet)tmErr(result.error||(result.lines||[]).map(x=>typeof x==='string'?x:x.text||'').join('\n')||'Replay command failed');return null;}
 const lines=(result.lines||[]).map(x=>typeof x==='string'?x:x.text||'');
 for(const line of lines.reverse()){try{return JSON.parse(line);}catch(_e){/* job logs also contain plain text */}}
 return null;
}
async function tmImport(){
 const file=prompt('Local path to a .gt7rec recording:');if(!file)return;
 const result=await tmRunCommand('telemetry-import',{file});if(result?.ok)await tmLoad();
}

// Deletes the selected recording, its CSV export and its lap index (racecast telemetry
// delete), after a confirmation; the list then reloads and selects the next recording.
async function tmDelete() {
  const profile=tmState.profile, rec = tmState.rec, row = tmState.recs.find(r => r.rec === rec);
  if (!row || row.recording) return;
  const laps = row.laps == null ? '' : ' (' + row.laps + ' laps)';
  if (!(await confirmModal('Delete the recording ' + rec + laps + '?\n'
      + 'This removes the recording, its context/history, CSV export, lap index, recording-owned AI analyses and their input packages. It cannot be undone.',
      {title: 'Delete recording', okLabel: 'Delete'}))) return;
  if (profile !== tmState.profile || rec !== tmState.rec) return;                 // the selection moved while the dialog was open
  const btn = $('tm-del');
  btn.disabled = true;
  let d;
  try {
    d = await (await fetch('/api/telemetry/delete', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rec})})).json();
  } catch (e) { d = {ok: false, error: 'Control Center not reachable.'}; }
  if (profile!==tmState.profile || rec!==tmState.rec) return;
  if (!d.ok) { tmErr(d.error || 'could not delete the recording'); btn.disabled = false; return; }
  tmState.rec = null;
  await tmLoad();
  if (d.notes && d.notes.length) tmNote('Deleted ' + d.deleted + '. ' + d.notes.join('; '));
}

async function tmSelectRec(rec) {
  if (tmState.rec !== rec) window.RacecastAI?.reset(tmState.profile);
  const seq = ++tmSeq.rec;
  tmSeq.b++;
  if (tmState.rec !== rec) { tmState.included = null; tmState.anchor = null; }
  tmState.rec = rec;
  tmState.recLaps = [];
  tmRenderRecs();
  tmErr('');
  $('tm-note').hidden = true;
  tmClear('Indexing…');                             // the first open runs the index job
  const d = await tmIndexed(rec, () => tmShared('/api/telemetry/laps?' + new URLSearchParams({rec})),
                            () => seq !== tmSeq.rec);
  if (seq !== tmSeq.rec) return;
  if (!d.ok) { tmClear(''); tmErr(d.error || 'could not read the recording'); return; }
  tmState.recLaps = (d.laps || []).concat(d.open_lap ? [d.open_lap] : []);
  tmState.recTrack = d.recording.track || null;
  tmState.sessionTracks = d.recording.sessions || null;
  tmRenderSummary(d.summary);
  const row = tmState.recs.find(r => r.rec === rec);
  if (row) {
    row.completed_laps = d.recording.completed_laps; row.partial_segments = d.recording.partial_segments;
    row.indexed = true; row.track = d.recording.track; row.laps = tmState.recLaps.length;
    tmRenderRecs();
  }
  const counted = tmState.recLaps.filter(l => l.comparison_eligible ?? l.pace_eligible ?? l.status !== 'not counted');
  const pick = (counted.length ? counted : tmState.recLaps).slice(-1)[0];
  if (!pick) tmClear('This recording has no laps.');
  $('tm-rec-track').textContent = tmTrackLabel(d.recording.track);
  if (!pick) tmShowSetTrack(d.recording.track);    // with a lap, lap B's session decides
  if (pick) await tmSelectB(pick);
}

// Opens a validated report reference in the existing lap-comparison view.
async function tmOpenReference(ref) {
  if (!ref || typeof ref.rec !== 'string' || !ref.rec || /[\\/]/.test(ref.rec)
      || !Number.isInteger(ref.session) || ref.session < 1 || !Number.isInteger(ref.lap) || ref.lap < 1
      || typeof ref.recording_id !== 'string' || !/^(?:[a-f0-9]{32}|[a-f0-9]{64})$/.test(ref.recording_id)
      || (ref.location_m != null && (!Number.isFinite(ref.location_m) || ref.location_m < 0))) {
    tmErr('Invalid report telemetry reference.'); return false;
  }
  const profile = ref.profile || tmState.profile;
  if (profile !== tmState.profile) {
    const message = 'This reference belongs to another profile. Select that profile explicitly first.';
    tmErr(message); alertModal(message, {title:'Report reference'}); return false;
  }
  if (!tmState.loaded) await tmLoad();
  if (profile !== tmState.profile) return false;
  showView('telemetry');
  const stem = value => String(value).replace(/\.gt7rec$/, '');
  const recording = tmState.recs.find(r => stem(r.rec) === stem(ref.rec) && !r.recording);
  if (!recording) { tmErr('The referenced recording is unavailable in this profile.'); return false; }
  await tmSelectRec(recording.rec);
  if (profile !== tmState.profile || tmState.rec !== recording.rec) return false;
  const lap = tmState.recLaps.find(l => l.session === ref.session && l.lap === ref.lap);
  if (!lap) { tmErr('The referenced lap is unavailable.'); return false; }
  await tmSelectB(lap);
  if (profile !== tmState.profile || tmState.b !== tmKey(lap)) return false;
  if (tmState.lapB?.recording_id !== ref.recording_id) {
    tmClear('The recording identity changed; the historical report snapshot is retained.'); return false;
  }
  if (ref.location_m != null && tmState.chart) {
    const chart = tmState.chart, box = $('tm-charts').getBoundingClientRect();
    tmHover({clientX: box.left + chart.X(Math.min(ref.location_m, chart.maxD)) / chart.W * box.width});
  }
  return true;
}


function tmRenderLaps() {
  const box = $('tm-laps');
  box.textContent = '';
  const laps = tmState.recLaps;
  if (!laps.length) { box.textContent = 'No laps in this recording.'; return; }
  // The car and track most laps share stay off the rows; only a lap that differs names its own.
  const usual = f => {
    const n = new Map();
    laps.forEach(l => n.set(f(l), (n.get(f(l)) || 0) + 1));
    return [...n.entries()].sort((x, y) => y[1] - x[1])[0][0];
  };
  const car = usual(l => l.car || ''), track = usual(tmLapTrack);
  const fast = laps.filter(l => (l.comparison_eligible ?? l.pace_eligible ?? l.status !== 'not counted') && l.time_s != null)
    .reduce((m, l) => (m && m.time_s <= l.time_s ? m : l), null);
  let sel = null;
  laps.forEach(l => {
    const nc = !(l.comparison_eligible ?? l.pace_eligible ?? l.status !== 'not counted'), key = tmKey(l);
    const contextual = l.pace_eligible === true && l.comparison_eligible === false;
    const b = document.createElement('button');
    b.className = 'tmitem' + (nc ? ' nc' : '') + (key === tmState.b ? ' sel'
      : key === tmState.a ? ' is-a' : '');
    const name = document.createElement('span');
    name.textContent = 'Lap ' + l.lap + (l.session > 1 ? ' · S' + l.session : '');
    const badge = document.createElement('span');
    badge.className = 'tmbadge' + (l.status === 'reference' ? ' ref' : '');
    badge.textContent = l.capture_complete === false ? 'partial' : l.lap_role === 'pit' ? 'pit'
      : nc ? (contextual ? 'not for comparison' : 'not for pace') : l.status === 'reference' ? 'best so far' : 'pace';
    badge.title = nc && contextual ? 'Excluded from default comparison: '
      + ([l.analysis_role !== 'regular' ? l.analysis_role : '', ...(l.context_warnings || [])].filter(Boolean).join('; ') || 'context conditions')
      : nc ? 'Excluded from pace: ' + ((l.reasons || [l.reason]).filter(Boolean).join('; ') || 'no reason recorded')
      : l.status === 'reference' ? 'Reference: the fastest lap so far when it was driven; '
        + 'the live HUD delta ran against it' : '';
    const time = document.createElement('span');
    time.className = 'tmtime' + (l === fast ? ' best' : '');
    time.textContent = tmTime(l.time_s);
    if (l === fast) time.title = 'Fastest counted lap of this recording';
    b.append(name, badge, time);
    const odd = [l.analysis_role === 'warmup' ? 'Warmup lap' : l.lap_role === 'first' ? 'First lap' : '',
                 l.compounds?.length ? l.compounds.join(' / ') : '',
                 l.fuel_start_l != null ? 'Start fuel '+l.fuel_start_l.toFixed(1)+' L (measured)' : 'Start fuel unknown',
                 l.tyre_age_laps != null ? 'Tyre age: '+l.tyre_age_laps+' lap boundaries since declared initial stint / tyre service' : 'Tyre age unknown',
                 ...(l.context_warnings || []),
                 l.after_service && !l.compound ? 'After inferred service; tyres unconfirmed' : '',
                 l.time_s == null && l.relay_time_s != null ? tmTime(l.relay_time_s) + ' captured' : '',
                 nc ? (l.reasons || [l.reason]).filter(Boolean).join('; ') : '',
                 l.trace_complete === false && l.capture_complete ? 'Partial distance trace' : '',
                 l.trace_complete && l.time_valid === false ? 'Invalid comparison clock' : '',
                 (l.car || '') !== car ? l.car || 'car unknown' : '',
                 tmLapTrack(l) !== track ? tmLapTrack(l) : ''].filter(Boolean);
    if (odd.length) {
      const sub = document.createElement('span');
      sub.className = 'tmsub';
      sub.textContent = sub.title = odd.join(' · ');
      b.append(sub);
    }
    b.onclick = () => tmSelectB(l);
    box.appendChild(b);
    if (key === tmState.b) sel = b;
  });
  // Lap B is often the last lap: scroll the list, never the page, to keep it in view.
  if (sel) {
    const r = sel.getBoundingClientRect(), bx = box.getBoundingClientRect();
    if (r.top < bx.top || r.bottom > bx.bottom) {
      box.scrollTop += r.top - bx.top - (box.clientHeight - r.height) / 2;
    }
  }
}

// The fastest pool lap other than B: comparing a lap with itself shows nothing.
function tmDefaultA(bKey) {
  const others = ((tmState.pool && tmState.pool.laps) || [])
    .filter(l => tmKey(l) !== bKey && l.time_s != null);
  others.sort((x, y) => x.time_s - y.time_s);
  return others.length ? tmKey(others[0]) : null;
}

async function tmSelectB(l) {
  const seq = ++tmSeq.b;
  const key = tmKey(l);
  tmState.anchor = l;
  tmState.b = key;
  tmRenderLaps();
  if (l.rec === tmState.rec) tmShowSetTrack(tmSessionTrack(l.session), l.session);
  else { tmSeq.track++; $('tm-settrack').hidden = true; }   // a lap B of another recording
  tmClearPair('Loading comparable laps…');
  const q = new URLSearchParams({car: l.car_id == null ? '' : String(l.car_id),
                                 track: l.track_id == null ? '' : l.track_id});
  if (tmState.included === null) tmState.included = new Set([tmState.rec || l.rec]);
  q.set('recs', [...tmState.included].join(','));
  q.set('rec', l.rec); q.set('session', String(l.session)); q.set('lap', String(l.lap));
  if ($('tm-compare-all').checked) q.set('compare_all', '1');
  let p = await tmShared('/api/telemetry/laps?' + q);
  if (seq !== tmSeq.b) return;
  if (p.ok && p.unindexed && tmMayIndex(p)) {   // asks once: a broken file stays unindexed
    $('tm-sec-sub').textContent = 'Indexing ' + p.unindexed
      + (p.unindexed === 1 ? ' recording…' : ' recordings…');
    const job = await tmIndexAll(p.data_version);
    if (seq !== tmSeq.b) return;
    if (job.error) tmErr(job.error);
    else p = await tmShared('/api/telemetry/laps?' + q);
    if (seq !== tmSeq.b) return;
  }
  if (!p.ok) tmErr(p.error || 'could not load the comparable laps');
  tmState.pool = p.ok ? p : {laps: [], best_sectors: [], theoretical_best: null};
  $('tm-conditions').textContent = p.ok ? (p.comparison_warnings || []).join(' · ') : '';
  tmState.available = p.recordings || [];
  tmRenderRecordingMenu();
  if (!tmState.included.has(l.rec) && p.ok && p.laps.length) return tmSelectB(p.laps[0]);
  if (!tmState.included.has(l.rec)) tmState.b = null;
  tmState.a = tmDefaultA(key);
  tmRenderLaps();                                   // mark lap A
  tmFillPickers(l);
  await tmLoadPair();
}

function tmRecordingLabel(row) {
  let date = row.start_ts != null ? new Date(row.start_ts * 1000) : new Date(row.started || '');
  if (!Number.isFinite(date.getTime())) {
    const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/.exec(row.rec || '');
    if (match) date = new Date(+match[1], +match[2]-1, +match[3], +match[4], +match[5], +match[6]);
  }
  const pad = n => String(n).padStart(2, '0');
  const name = Number.isFinite(date.getTime())
    ? ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][date.getDay()] + ' ' + pad(date.getDate()) + '.'
      + pad(date.getMonth()+1) + '. ' + pad(date.getHours()) + ':' + pad(date.getMinutes())
    : row.rec;
  const count = row.completed_laps ?? row.laps;
  return name + (count != null ? ' · ' + count + (count === 1 ? ' lap' : ' laps') : '');
}
function tmRenderRecordingMenu() {
  const box = $('tm-recording-options'); box.textContent = '';
  const rows = tmState.available.slice();
  const own = tmState.recs.find(r => r.rec === tmState.rec);
  if (own && !rows.some(r => r.rec === own.rec)) rows.push(own);
  rows.sort((a,b) => (b.start_ts || 0) - (a.start_ts || 0));
  const chosen = rows.filter(r => tmState.included?.has(r.rec)).length;
  $('tm-recording-caption').textContent = chosen === 1 && tmState.included?.has(tmState.rec)
    ? 'Compare: this recording' : 'Compare: ' + chosen + ' of ' + rows.length + ' recordings';
  rows.forEach(row => {
    const label = document.createElement('label'), input = document.createElement('input');
    input.type = 'checkbox'; input.checked = tmState.included?.has(row.rec) || false;
    label.title = row.rec; label.append(input, document.createTextNode(tmRecordingLabel(row)));
    input.onchange = () => {
      if (input.checked) tmState.included.add(row.rec); else tmState.included.delete(row.rec);
      tmReloadRecordingPool();
    };
    box.append(label);
  });
}
function tmChooseRecordings(all) {
  tmState.included = new Set(all ? tmState.available.map(r => r.rec).concat(tmState.rec ? [tmState.rec] : [])
                               : tmState.rec ? [tmState.rec] : []);
  return tmReloadRecordingPool();
}
async function tmReloadRecordingPool() {
  const known = (tmState.pool?.laps || []).concat(tmState.recLaps);
  const anchor = tmState.anchor || known.find(l => tmKey(l) === tmState.b);
  if (!anchor) return;
  const pick = laps => {
    const included = laps.filter(l => tmState.included.has(l.rec) && l.car_id === anchor.car_id
      && l.track_id === anchor.track_id && (anchor.track_id != null
        || l.rec === anchor.rec && l.session === anchor.session));
    return included.find(l => tmKey(l) === tmState.b)
      || included.filter(l => l.comparison_eligible ?? l.pace_eligible ?? l.status !== 'not counted').slice(-1)[0]
      || included.slice(-1)[0];
  };
  let lap = pick(known);
  if (!lap && tmState.included.size) {
    const seq = ++tmSeq.b, rec = [...tmState.included][0];
    tmClearPair('Loading included recording…'); tmState.b = null;
    const d = await tmIndexed(rec, () => tmShared('/api/telemetry/laps?' + new URLSearchParams({rec})),
                              () => seq !== tmSeq.b);
    if (seq !== tmSeq.b) return;
    if (!d.ok) tmErr(d.error || 'Could not read the included recording.');
    lap = pick((d.laps || []).concat(d.open_lap ? [d.open_lap] : []));
    if (!lap) {
      tmState.pool = {laps: [], best_sectors: [], theoretical_best: null};
      tmRenderRecordingMenu(); tmPlaceholder('tm-a', 'no other lap to compare');
      tmPlaceholder('tm-b', 'no included lap'); tmRender(); return;
    }
  }
  await tmSelectB(lap || anchor);
}

function tmFillPickers(lapB) {
  const pool = tmState.pool.laps || [];
  const bKey = tmKey(lapB);
  const forA = pool.filter(l => tmKey(l) !== bKey);
  const forB = pool.some(l => tmKey(l) === bKey) || !tmState.included?.has(lapB.rec) ? pool : pool.concat([lapB]);
  const eligible = pool.filter(l => l.time_s != null && (l.pace_eligible ?? l.status !== 'not counted'));
  const overall = Math.min(...eligible.map(l => l.time_s));
  [['tm-a', forA, tmState.a], ['tm-b', forB, tmState.b]].forEach(([id, list, cur]) => {
    if (!list.length) { tmPlaceholder(id, id === 'tm-a' ? 'no other lap to compare' : 'no included lap'); return; }
    const sel = $(id); sel.textContent = ''; sel.disabled = false;
    const names = [...new Set(list.map(l => l.rec))];
    names.sort((a,b) => (tmState.recs.find(r=>r.rec===b)?.start_ts || 0) - (tmState.recs.find(r=>r.rec===a)?.start_ts || 0));
    names.forEach(rec => {
      const group = document.createElement('optgroup');
      const row = tmState.available.find(r=>r.rec===rec) || tmState.recs.find(r=>r.rec===rec) || {rec};
      group.label = tmRecordingLabel(row); group.title = rec;
      const best = Math.min(...eligible.filter(l=>l.rec===rec).map(l=>l.time_s));
      list.filter(l=>l.rec===rec).sort((a,b)=>a.session-b.session || a.lap-b.lap).forEach(l => {
        const option = document.createElement('option'); option.value = tmKey(l); option.title = rec;
        const counted = eligible.some(e=>tmKey(e)===tmKey(l));
        option.textContent = ['Lap ' + l.lap + (l.session > 1 ? ' S' + l.session : ''), tmTime(l.time_s),
          counted && l.time_s === best ? 'recording best' : '',
          counted && l.time_s === overall ? 'overall best' : '',
          !counted ? 'not counted' : ''].filter(Boolean).join(' · ');
        group.append(option);
      });
      sel.append(group);
    });
    sel.value = cur || '';
  });
}

async function tmRefreshContext(rec) {
  if (!rec || ![tmState.rec,tmState.lapA?.rec,tmState.lapB?.rec].includes(rec)) return;
  const selected = tmState.b;
  tmShiftState.generation++;tmShiftState.analysis=null;tmShiftState.pair=null;
  $('tm-shift-values').textContent='';
  tmDropLaps();
  if (rec !== tmState.rec) {await tmReloadRecordingPool();return;}
  await tmSelectRec(rec);
  if (tmState.rec !== rec) return;
  const previous = tmState.recLaps.find(l => tmKey(l) === selected);
  if (previous) await tmSelectB(previous);
}

function tmComparisonMode() {
  tmDropLaps();
  const lap = (tmState.pool?.laps || []).concat(tmState.recLaps).find(l => tmKey(l) === tmState.b);
  if (lap) tmSelectB(lap);
}

function tmPick(which, key) {
  tmState[which] = key || null;
  if (which === 'b' && tmState.pool) {
    const lb = (tmState.pool.laps || []).concat(tmState.recLaps).find(l => tmKey(l) === key);
    if (lb) {
      return tmSelectB(lb);
    }
  }
  tmRenderLaps();
  return tmLoadPair();
}

// Resolves to {lap} or {error}; never writes page state, so a stale answer cannot leak.
async function tmFetchLap(key) {
  if (tmLapCache.has(key)) return {lap: tmLapCache.get(key)};
  const gen = tmLapGen;
  const [rec, session, lap] = key.split('|');
  const d = await tmIndexed(rec,
    () => tmShared('/api/telemetry/lap?' + new URLSearchParams({rec, session, lap})),
    () => gen !== tmLapGen);
  if (d.stale) return {error: ''};
  if (!d.ok) return {error: d.error || 'could not load the lap'};
  if (gen === tmLapGen) tmLapCache.set(key, d.lap);
  return {lap: d.lap, sectorM: d.sector_m};
}

async function tmLoadPair() {
  const seq = ++tmSeq.pair;
  const want = [tmState.a, tmState.b];
  const [a, b] = await Promise.all(want.map(k => (k ? tmFetchLap(k) : {lap: null})));
  if (seq !== tmSeq.pair || want[0] !== tmState.a || want[1] !== tmState.b) return;
  const err = a.error || b.error;
  if (err) tmErr(err);
  tmState.sectorM = a.sectorM || b.sectorM || tmState.sectorM;
  tmState.lapA = a.lap || null;
  tmState.lapB = b.lap || null;
  $('tm-a').value = tmState.a || '';
  $('tm-b').value = tmState.b || '';
  tmRender();
  workspaceSelection();
}

function tmRender() {
  const B = tmState.lapB;
  $('tm-map-sub').textContent = B ? tmLapTrack(B) : '';
  tmRenderCharts();
  tmRenderMap();
  tmRenderSectors();
  tmRenderLargerSectors();
  tmChannelRefresh();
  tmShiftRefresh();
  if(B)tmSeek(tmState.positionD);
}

function tmRenderSectors() {
  const t = $('tm-sectors');
  t.textContent = '';
  const A = tmState.lapA, B = tmState.lapB;
  $('tm-sec-legend').hidden = !B;
  if (!B) return;
  const best = (tmState.pool && tmState.pool.best_sectors) || [];
  const sa = A ? A.sectors || [] : [], sb = B.sectors || [];
  const head = t.createTHead().insertRow();
  ['Sector', 'km', 'A', 'B', 'B-A', 'Best'].forEach(h => {
    const th = document.createElement('th'); th.textContent = h; head.appendChild(th);
  });
  const body = t.createTBody();
  const f = tmTime;
  const sm = tmState.sectorM || 200, n = Math.max(sa.length, sb.length);
  // The last sector runs to the end of lap B, which is rarely a whole sector length.
  const trB = tmTrace(B), endM = trB.length ? trB[trB.length - 1].d : n * sm;
  const km = i => (i * sm / 1000).toFixed(1) + '–' + (Math.min((i + 1) * sm, i === n - 1 ? endM : Infinity) / 1000).toFixed(1);
  for (let i = 0; i < n; i++) {
    const ta = sa[i], tb = sb[i], bs = best[i];
    const diff = ta != null && tb != null ? tb - ta : null;
    const row = body.insertRow();
    [String(i + 1), km(i), f(ta), f(tb), tmSigned(diff), f(bs)].forEach(v => { row.insertCell().textContent = v; });
    if (tmSecClass(diff) !== 'even') row.cells[4].className = tmSecClass(diff);
    if (tb != null && bs != null && Math.abs(tb - bs) < 0.0005) row.cells[3].className = 'best';
  }
  const foot = t.createTFoot().insertRow();
  const lapDiff = A && A.time_s != null && B.time_s != null ? B.time_s - A.time_s : null;
  ['Lap', '', tmTime(A ? A.time_s : null), tmTime(B.time_s), tmSigned(lapDiff),
   tmTime(tmState.pool ? tmState.pool.theoretical_best : null)].forEach(v => {
    foot.insertCell().textContent = v;
  });
  foot.cells[5].title = 'Diagnostic mini-sector sum; fine-segment composition';
  if (tmSecClass(lapDiff) !== 'even') foot.cells[4].className = tmSecClass(lapDiff);
  const laps = tmState.pool ? tmState.pool.laps || [] : [];
  const alone = !laps.some(l => tmKey(l) !== tmKey(B));
  $('tm-sec-sub').textContent = (tmState.sectorM || 200) + ' m'
    + (alone ? ' · no other lap' : ' · best of ' + laps.length + ' laps');
}
// Pairs only stations at the same distance, so closing points of different lap lengths never meet.
function tmDelta(a, b) {
  const n = Math.min(a.trace.length, b.trace.length), out = [];
  for (let i = 0; i < n; i++) {
    if (a.trace[i].d !== b.trace[i].d) continue;
    out.push({d: a.trace[i].d, delta: Math.round((b.trace[i].t - a.trace[i].t) * 1000) / 1000});
  }
  return out;
}

function tmPath(rows, key, X, Y, step) {
  let d = '', pen = false;
  rows.forEach(r => {
    const v = r[key];
    if (v == null) { pen = false; return; }
    const x = X(r.d).toFixed(1), y = Y(v).toFixed(1);
    d += !pen ? 'M' + x + ' ' + y : step ? 'H' + x + 'V' + y : 'L' + x + ' ' + y;
    pen = true;
  });
  return d || 'M0 0';
}

function tmDeltaArea(svg, delta, X, Y, minD=0, endD=Infinity) {
  const y0 = Y(0), x0 = Math.max(X(minD), X(delta[0].d)), x1 = Math.min(X(endD), X(delta[delta.length - 1].d));
  let d = 'M' + x0.toFixed(1) + ' ' + y0.toFixed(1);
  delta.forEach(r => { d += 'L' + X(r.d).toFixed(1) + ' ' + Y(r.delta).toFixed(1); });
  d += 'L' + x1.toFixed(1) + ' ' + y0.toFixed(1) + 'Z';
  const defs = tmEl('defs', {}, svg);
  tmEl('rect', {x: x0, y: y0 - 2000, width: x1 - x0, height: 2000}, tmEl('clipPath', {id: 'tm-clip-up'}, defs));
  tmEl('rect', {x: x0, y: y0, width: x1 - x0, height: 2000}, tmEl('clipPath', {id: 'tm-clip-dn'}, defs));
  tmEl('path', {d, class: 'tm-loss', 'clip-path': 'url(#tm-clip-up)'}, svg);   // B behind
  tmEl('path', {d, class: 'tm-gain', 'clip-path': 'url(#tm-clip-dn)'}, svg);   // B ahead
}

function tmTrace(l) { return (l && l.trace) || []; }

function tmRenderCharts() {
  const svg = $('tm-charts');
  svg.textContent = '';
  tmState.chart = tmState.cursor = null;
  const A = tmState.lapA, B = tmState.lapB;
  const ta = tmTrace(A), tb = tmTrace(B);
  if (!tb.length) return;
  const W = Math.max(300, Math.round(svg.getBoundingClientRect().width) || 760);
  const H = TM_PAD.t + TM_CH.reduce((s, c) => s + c.h + TM_PAD.gap, 0) + TM_PAD.b;
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  const last = t => (t.length ? t[t.length - 1].d : 0);
  const maxD = Math.max(last(ta), last(tb)) || 1;
  const zoom=tmState.zoom, minD=zoom && zoom.start<maxD ? zoom.start : 0, endD=zoom && zoom.end<=maxD && zoom.end>minD ? zoom.end : maxD;
  const X = d => TM_PAD.l + (d-minD) / (endD-minD) * (W - TM_PAD.l - TM_PAD.r);
  const defs=tmEl('defs',{},svg), clip=tmEl('clipPath',{id:'workspace-chart-clip'},defs);
  tmEl('rect',{x:TM_PAD.l,y:0,width:W-TM_PAD.l-TM_PAD.r,height:H},clip);
  $('workspace-cursor').min=String(minD);$('workspace-cursor').max=String(endD);
  const delta = ta.length ? tmDelta({trace: ta}, {trace: tb}) : [];
  const panels = [];
  let y = TM_PAD.t;
  TM_CH.forEach(c => {
    const series = c.key === 'delta' ? (delta.length ? [{cls: 'tm-b', rows: delta}] : [])
      : [ta.length && {cls: 'tm-a', rows: ta}, {cls: 'tm-b', rows: tb}].filter(Boolean);
    let vmin = Infinity, vmax = -Infinity;
    series.forEach(s => s.rows.forEach(r => {
      const v = r[c.key];
      if (v != null) { if (v < vmin) vmin = v; if (v > vmax) vmax = v; }
    }));
    const any = vmin <= vmax;
    let lo = c.lo != null ? c.lo : (any ? vmin : 0);
    let hi = c.hi != null ? c.hi : (any ? vmax : 1);
    if (c.sym) {
      const m = Math.max(0.1, any ? Math.max(-vmin, vmax) : 0) * 1.1;
      lo = -m; hi = m;
    } else if (c.hi == null) {
      hi = c.step ? Math.max(hi, 1) + 0.5 : Math.max(hi, lo + 1) * 1.05;
    }
    const top = y;
    const Y = v => top + c.h - (v - lo) / (hi - lo) * c.h;
    tmEl('rect', {x: TM_PAD.l, y: top, width: W - TM_PAD.l - TM_PAD.r, height: c.h, class: 'tm-frame'}, svg);
    if (series.length && lo < 0 && hi > 0) {
      tmEl('line', {x1: TM_PAD.l, x2: W - TM_PAD.r, y1: Y(0), y2: Y(0), class: 'tm-zero'}, svg);
    }
    tmEl('text', {x: 4, y: top + 13}, svg).textContent = c.label;
    tmEl('text', {x: 4, y: top + 27, class: 'tm-unit'}, svg).textContent = c.unit;
    let lastTy = -Infinity;
    tmTicks(c, vmin, vmax).forEach(v => {
      const ty = Math.max(top + 10, Math.min(top + c.h - 2, Y(v) + 3.5));
      if (ty - lastTy < 10) return;                  // two labels would overlap: keep the upper
      lastTy = ty;
      tmEl('text', {x: TM_PAD.l - 6, y: ty, 'text-anchor': 'end', class: 'tm-axis'}, svg)
        .textContent = tmTickText(c, v);
    });
    if (c.key === 'delta') {
      if (delta.length) {
        tmDeltaArea(svg, delta, X, Y, minD, endD);
        const visible = delta.filter(row=>row.d>=minD && row.d<=endD).at(-1);
        if(visible){const caption=tmEl('text', {x: W - TM_PAD.r - 6, y: top + c.h - 6, 'text-anchor': 'end',
                      class: 'tm-final ' + tmSecClass(visible.delta)}, svg);caption.textContent = tmSigned(visible.delta) + ' s';
          caption.setAttribute('aria-label','Shared trace delta through '+visible.d+' m');}
      } else tmEl('text', {x: TM_PAD.l + 8, y: top + c.h / 2 + 4, class: 'tm-unit'}, svg).textContent = 'no lap A to compare';
    }
    series.forEach(s => tmEl('path', {d: tmPath(s.rows, c.key, X, Y, c.step), class: s.cls,'clip-path':'url(#workspace-chart-clip)'}, svg));
    const bg = tmEl('rect', {x: W - TM_PAD.r, y: top + 2, width: 0, height: 15, rx: 3, class: 'tm-readbg'}, svg);
    panels.push({c, bg, read: tmEl('text', {x: W - TM_PAD.r - 6, y: top + 13, 'text-anchor': 'end', class: 'tm-read'}, svg)});
    y += c.h + TM_PAD.gap;
  });
  // Ticks at least ~70 px apart, so the km labels never collide on a narrow pane.
  const room = (W - TM_PAD.l - TM_PAD.r) / 70;
  const tick = [200, 500, 1000, 2000, 5000].find(t => maxD / t <= room) || 10000;
  for (let d = Math.ceil(minD/tick)*tick; d <= endD; d += tick) {
    const edge = X(d) > W - TM_PAD.r - 30;       // the last label must not run off the right edge
    tmEl('text', {x: edge ? W - TM_PAD.r : X(d), y: H - 6, 'text-anchor': edge ? 'end' : 'middle'}, svg)
      .textContent = (d / 1000).toFixed(1) + ' km';
  }
  tmState.cursor = tmEl('line', {class: 'tm-cursor', x1: -10, x2: -10, y1: TM_PAD.t, y2: H - TM_PAD.b}, svg);
  tmState.chart = {X, minD, maxD:endD, W, panels};
  workspaceSelection(false);
}

function tmHover(ev) {
  const ch = tmState.chart;
  if (!ch) return;
  const box = $('tm-charts').getBoundingClientRect();
  const px = (ev.clientX - box.left) * ch.W / box.width;
  const d = Math.max(ch.minD, Math.min(ch.maxD, ch.minD+(px - TM_PAD.l) / (ch.W - TM_PAD.l - TM_PAD.r) * (ch.maxD-ch.minD)));
  workspaceCursor(d);
  tmState.positionD=d;tmState.positionKey=tmPositionKey();
  const x = ch.X(d).toFixed(1);
  tmState.cursor.setAttribute('x1', x);
  tmState.cursor.setAttribute('x2', x);
  const ra = tmAtDistance(tmState.lapA,d), rb = tmAtDistance(tmState.lapB,d);
  $('tm-map-position').textContent=tmPassageLabel(tmState.lapB,rb?.d??d)+(tmMapPt(rb)?'':' · position unavailable');
  tmState.noteCursorKey = tmState.lapB ? tmKey(tmState.lapB) : null;
  tmState.noteCursor = rb && tmMapPt(rb) ? {x: rb.x, z: rb.z, d: rb.d, t: rb.t} : null;
  ch.panels.forEach(p => {
    const k = p.c.key;
    const txt = k === 'delta' ? (ra && rb ? tmSigned(rb.t - ra.t) + ' s' : '')
      : [ra, rb].map(r => (r && r[k] != null ? Number(r[k]).toFixed(k === 'gear' ? 0 : 1) : '-')).join(' / ');
    p.read.textContent = txt;
    tmReadBg(p, ch.W);
  });
  tmMapCursor(ra, rb);
}

function tmLeave() {
  // Keep the selected distance visible while the driver reads the map or findings.
}
function tmPositionKey(){return tmState.lapB?tmKey(tmState.lapB)+'|'+(tmState.lapB.recording_id||''):null;}
function tmAtDistance(lap,distance){
  const station=Math.round(distance/TM_STEP),row=tmTrace(lap)[station];
  return row&&Math.abs(row.d-station*TM_STEP)<TM_STEP/2?row:null;
}
function tmPassageLabel(lap,distance){
  const prefix=lap?.track_definition?.reviewed?'T':'P';
  const corners=(lap?.corner_metrics||[]).filter(c=>Number.isFinite(c.apex_m)&&Number.isInteger(c.number)).sort((a,b)=>a.apex_m-b.apex_m);
  const inside=corners.filter(c=>Number.isFinite(c.entry_m)&&Number.isFinite(c.exit_m)&&c.entry_m<=distance&&distance<=c.exit_m)
    .sort((a,b)=>Math.abs(a.apex_m-distance)-Math.abs(b.apex_m-distance))[0];
  const metre=Math.round(distance)+' m';if(inside)return prefix+inside.number+' · '+metre;
  const before=[...corners].reverse().find(c=>c.apex_m<distance),after=corners.find(c=>c.apex_m>distance);
  return before&&after?'Between '+prefix+before.number+' and '+prefix+after.number+' · '+metre:metre;
}
function tmSeek(distance){
  const chart=tmState.chart;if(!chart)return;
  const box=$('tm-charts').getBoundingClientRect();if(!box.width)return;
  tmHover({clientX:box.left+chart.X(Math.max(chart.minD,Math.min(chart.maxD,distance)))/chart.W*box.width});
}
function tmRestoreMapPosition(){
  if(tmState.positionKey!==tmPositionKey()){tmState.positionKey=tmPositionKey();tmState.positionD=0;}
  const d=tmState.positionD,point=tmAtDistance(tmState.lapB,d);
  tmMapCursor(tmAtDistance(tmState.lapA,d),point);
  $('tm-map-position').textContent=tmPassageLabel(tmState.lapB,point?.d??d)+(tmMapPt(point)?'':' · position unavailable');
}

// Sized from the character count (11px mono is ~6.7px wide), so a hover never forces a layout.
function tmReadBg(p, W) {
  const w = p.read.textContent ? p.read.textContent.length * 6.7 + 8 : 0;
  p.bg.setAttribute('x', W - TM_PAD.r - 2 - w);
  p.bg.setAttribute('width', w);
}

function tmMapPt(p) { return p && Number.isFinite(p.x) && Number.isFinite(p.z); }
function tmMapW() { return Math.max(200, Math.round($('tm-map').getBoundingClientRect().width) || 320); }

function tmRenderMap() {
  const svg = $('tm-map');
  svg.textContent = '';
  tmState.map = null;
  if(tmState.positionKey!==tmPositionKey()){tmState.positionKey=tmPositionKey();tmState.positionD=0;}
  const A = tmState.lapA, B = tmState.lapB;
  const ta = tmTrace(A), tb = tmTrace(B);
  $('tm-map-position').textContent='Position unavailable for this lap.';
  $('tm-map-numbering').textContent='';$('tm-map-legend').hidden=true;
  if (!tb.length) return;
  const W = tmMapW(), pad = 12;
  let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity, hasB = false;
  [ta, tb].forEach(t => t.forEach(p => {
    if (!tmMapPt(p)) return;
    if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x;
    if (p.z < z0) z0 = p.z; if (p.z > z1) z1 = p.z;
    hasB = hasB || t === tb;
  }));
  if (!hasB) {
    svg.setAttribute('viewBox', '0 0 ' + W + ' 24');
    tmEl('text', {x: 0, y: 15}, svg).textContent = 'no position data in this lap';
    $('tm-map-position').textContent='Position unavailable for this lap.';
    $('tm-map-numbering').textContent='';
    return;
  }
  const s = (W - 2 * pad) / (Math.max(x1 - x0, z1 - z0) || 1);
  const sides=[0,0];for(const corner of B.track_definition?.corners||[]){if(tmMapPt(corner.apex))sides[corner.apex.x>=(x0+x1)/2?1:0]++;}
  const H = Math.max(72,Math.round((z1-z0)*s+2*pad),Math.max(...sides)*16+28);
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  const ox = (W - (x1 - x0) * s) / 2, oz = (H - (z1 - z0) * s) / 2;   // centre the shorter axis
  const P = p => [ox + (p.x - x0) * s, oz + (p.z - z0) * s];
  const line = pts => {
    let d = '', pen = false;
    pts.forEach(p => {
      if (!tmMapPt(p)) { pen = false; return; }
      d += (pen ? 'L' : 'M') + P(p).map(v => v.toFixed(1)).join(' ');
      pen = true;
    });
    return d;
  };
  const per = (tmState.sectorM || 200) / TM_STEP;      // stations per mini-sector
  const sa = A ? A.sectors || [] : [];
  (B.sectors || []).forEach((secB, i) => {
    const d = line(tb.slice(Math.round(i * per), Math.round((i + 1) * per) + 1));
    if (!d) return;
    const secA = sa[i];
    const diff = secA != null && secB != null ? secB - secA : null;
    const path = tmEl('path', {d, class: 'tm-seg ' + tmSecClass(diff)}, svg);
    tmEl('title', {}, path).textContent = 'Sector ' + (i + 1) + (diff == null ? '' : ': ' + tmSigned(diff) + ' s');
  });
  const da = line(ta);
  if (da) tmEl('path', {d: da, class: 'tm-a'}, svg);    // a thin line over B's colours
  $('tm-map-legend').hidden = false;
  const [sx, sy] = P(tb.find(tmMapPt));
  tmEl('circle', {cx: sx, cy: sy, r: 3.5, class: 'tm-line'}, svg);   // start/finish line
  tmCornerLabels(svg,B.track_definition?.corners||[],P,W,H,!!B.track_definition?.reviewed);
  $('tm-map-numbering').textContent=B.track_definition?.corners?.length?
    (B.track_definition.reviewed?'Turn numbers from saved reviewed markers.':'P numbers are provisional geometry markers.'):'No saved turn numbering for this track.';
  const dot = cls => tmEl('circle', {r: 5, cx: 0, cy: 0, visibility: 'hidden', class: cls}, svg);
  tmState.map = {P, W, dotA: dot('tm-dot-a'), dotB: dot('tm-dot-b')};
  tmRestoreMapPosition();
}

function tmCornerLabels(svg,corners,project,W,H,reviewed){
  const groups=[[],[]],seen=new Set();
  for(const corner of corners){if(!Number.isInteger(corner.number)||corner.number<=0||seen.has(corner.number)||!tmMapPt(corner.apex))continue;
    const [x,y]=project(corner.apex);if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||x>W||y<0||y>H)continue;
    seen.add(corner.number);groups[x>=W/2?1:0].push({corner,x,y});}
  groups.forEach((rows,side)=>{rows.sort((a,b)=>a.y-b.y);const gap=Math.min(16,(H-28)/Math.max(1,rows.length-1));let previous=-100;
    rows.forEach(({corner,x,y},i)=>{const labelY=Math.min(H-10-(rows.length-i-1)*gap,Math.max(16+i*gap,y+4,previous+gap));previous=labelY;
      tmEl('path',{d:'M'+x+','+y+'L'+(side?W-38:38)+','+(labelY-4),class:'tm-corner-leader'},svg);
      const dot=tmEl('circle',{cx:x,cy:y,r:3,class:'tm-marker'},svg);tmEl('title',{},dot).textContent=(reviewed?'Turn ':'Provisional marker ')+corner.number+(corner.name?' · '+corner.name:'');
      tmEl('text',{x:side?W-10:10,y:labelY,'text-anchor':side?'end':'start',class:'tm-corner-label'},svg).textContent=(reviewed?'T':'P')+corner.number;
    });});
}

// A lap that does not reach the cursor distance (r null) shows no dot.
function tmMapCursor(ra, rb) {
  const m = tmState.map;
  if (!m) return;
  [[m.dotA, ra], [m.dotB, rb]].forEach(([dot, r]) => {
    if (!tmMapPt(r)) { dot.setAttribute('visibility', 'hidden'); return; }
    const [x, y] = m.P(r);
    dot.setAttribute('cx', x.toFixed(1));
    dot.setAttribute('cy', y.toFixed(1));
    dot.setAttribute('visibility', 'visible');
  });
}

// Redraws what a width change would distort; a hidden view (width 0) waits for showView.
function tmRefit() {
  if (tmState.summary) tmRenderSummary(tmState.summary);
  if (!tmState.lapB) return;
  const cw = Math.round($('tm-charts').getBoundingClientRect().width);
  if (cw && (!tmState.chart || Math.max(300, cw) !== tmState.chart.W)) tmRenderCharts();
  const mw = Math.round($('tm-map').getBoundingClientRect().width);
  if (mw && (!tmState.map || Math.max(200, mw) !== tmState.map.W)) tmRenderMap();
  tmSeek(tmState.positionD);
}

// The track of one GT7 session of the open recording; an index without per-session
// tracks falls back to the recording's.
function tmSessionTrack(session) {
  const all = tmState.sessionTracks, k = String(session);
  return all && Object.prototype.hasOwnProperty.call(all, k) ? all[k] : tmState.recTrack;
}

// The Set track row for an unknown or ambiguous session (or a recording without laps);
// one recording often holds several tracks, so it acts on lap B's session. Candidates
// come first.
async function tmShowSetTrack(track, session = null) {
  const seq = tmSeq.rec, mine = ++tmSeq.track;
  const row = tmState.recs.find(r => r.rec === tmState.rec);
  $('tm-settrack').hidden = true;
  tmState.trackSession = session;
  if ((track && !track.candidates) || (row && row.recording)) return;
  if (!tmState.tracks) {
    const d = await tmShared('/api/telemetry/tracks');
    if (seq !== tmSeq.rec || mine !== tmSeq.track) return;
    if (!d.ok) { tmErr(d.error || 'could not load the track list'); return; }
    tmState.tracks = (d.tracks || []).slice().sort((a, b) =>
      a.track.localeCompare(b.track) || (a.layout || '').localeCompare(b.layout || ''));
  }
  const want = ((track && track.candidates) || []).map(c => String(c.id));
  const list = want.map(id => tmState.tracks.find(t => t.id === id)).filter(Boolean)
    .concat(tmState.tracks.filter(t => !want.includes(t.id)));
  const sel = $('tm-track-pick');
  sel.textContent = '';
  list.forEach(t => {
    const o = document.createElement('option');
    o.value = t.id;
    o.textContent = tmTrackName(t);
    sel.appendChild(o);
  });
  $('tm-settrack-session').textContent = session == null ? '' : 'Session ' + session;
  $('tm-settrack-session').hidden = session == null;
  $('tm-settrack').hidden = false;
}

async function tmLearn() {
  const sel = $('tm-track-pick'), rec = tmState.rec, seq = tmSeq.rec;
  const row = tmState.recs.find(r => r.rec === rec);
  if (!sel.value || !rec || (row && row.recording)) return;
  const label = sel.options[sel.selectedIndex].textContent, trackId = sel.value;
  const session = tmState.trackSession, what = session == null ? rec : 'session ' + session + ' of ' + rec;
  if (!(await confirmModal('Set the track of ' + what + ' to ' + label + '?\n'
      + 'Without a downloaded racing line for this layout, racecast learns it from the '
      + 'longest counted lap' + (session == null ? '' : ' of that session')
      + ', so later recordings on it are recognised.', {title: 'Set track'}))) return;
  if (seq !== tmSeq.rec) return;
  const btn = $('tm-learn');
  btn.disabled = true;
  try {
    const r = await tmIndexed(rec, async () => (await fetch('/api/telemetry/learn', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(session == null ? {rec, track_id: trackId}
                                           : {rec, track_id: trackId, session})})).json(), () => seq !== tmSeq.rec,
      on => { btn.textContent = on ? 'Indexing…' : 'Set track'; });
    if (r.ok) {                           // every cached index is now stale
      tmDropLaps();
      tmState.recs.forEach(x => { if (x.indexed) x.indexed = false; });
    }
    if (seq !== tmSeq.rec) return;
    if (!r.ok) { tmErr(r.error || 'could not set the track'); return; }
    tmErr('');
    tmSelectRec(rec);
    $('tm-note').textContent = (session == null ? rec : 'Session ' + session + ' of ' + rec)
      + ' now uses ' + label + '. ' + (r.learned
      ? 'racecast learned the line from its longest counted lap, so later recordings on it are recognised.'
      : 'The downloaded racing line for it stays as it is.');
    $('tm-note').hidden = false;
  } catch (e) {
    if (seq === tmSeq.rec) tmErr('Control Center not reachable.');
  } finally {
    btn.disabled = false;
  }
}

$('tm-charts').addEventListener('pointermove', tmHover);
$('tm-charts').addEventListener('pointerleave', tmLeave);
let tmResizeQueued = false;
window.addEventListener('resize', () => {
  if (currentView !== 'telemetry' || !(tmState.lapB || tmState.summary) || tmResizeQueued) return;
  tmResizeQueued = true;
  requestAnimationFrame(() => { tmResizeQueued = false; tmRefit(); });
});
// Expanded channel detail is loaded on demand, separate from the compact default charts.
const tmChannelState = {catalog:null, generation:0, details:null, pair:null};
function tmChannelStatus(text, error=false) {
 $('tm-channel-status').textContent=text;$('tm-channel-status').className=error?'enverr':'sub';
}
function tmChannelKeys() {return [...$('tm-channel-select').selectedOptions].map(o=>o.value);}
function tmChannelIdentity() {return JSON.stringify([tmState.profile,tmState.lapA&&tmKey(tmState.lapA),tmState.lapB&&tmKey(tmState.lapB),tmChannelKeys(),$('tm-channel-axis').value,$('tm-channel-start').value,$('tm-channel-end').value,$('tm-channel-diagnostics').checked]);}
function tmChannelInvalidate() {tmChannelState.generation++;tmChannelState.details=null;$('tm-channel-charts').textContent='';tmChannelStatus('Selection changed; load the window to apply it.');}
function tmChannelChoices() {
 const select=$('tm-channel-select'), old=tmChannelKeys();select.textContent='';
 for(const d of tmChannelState.catalog||[]){if(d.diagnostic!==$('tm-channel-diagnostics').checked)continue;
  const o=document.createElement('option');o.value=d.key;o.textContent=d.label+' · '+d.unit+(d.diagnostic?' · '+d.offset:'');
  o.title=d.source+(d.uncertainty?' · '+d.uncertainty:'');o.selected=old.includes(d.key)||(!old.length&&d.key==='rpm');select.append(o);}
 tmChannelState.generation++;tmChannelState.details=null;$('tm-channel-charts').textContent='';
 tmChannelStatus($('tm-channel-diagnostics').checked?'Raw diagnostic interpretations have unverified meanings or units.':'Select up to eight channels, including constants and flags.');
}
function tmChannelBounds() {
 const B=tmState.lapB, time=$('tm-channel-axis').value==='time';
 $('tm-channel-start').value='0';$('tm-channel-end').value=B?String(time?Math.min(30,B.end_t_s-B.start_t_s):B.length_m):'';
 tmChannelState.generation++;tmChannelState.details=null;$('tm-channel-charts').textContent='';
 tmChannelStatus(time?'Receive-clock seconds from the lap boundary. At most 30 seconds; every retained packet remains visible.':'Metres along the common reference geometry when available; otherwise driven distance.');
}
async function tmChannelOpen() {
 if(!$('tm-channel-box').open)return;
 if(!tmChannelState.catalog){const d=await tmGet('/api/telemetry/channels');
  if(!d.ok){tmChannelStatus(d.error||'Channel catalog unavailable',true);return;}
  tmChannelState.catalog=d.channels;tmChannelChoices();}
 tmChannelBounds();
 if(tmChannelKeys().length&&tmState.lapB)await tmChannelLoad();
}
function tmChannelRefresh() {
 const pair=JSON.stringify([tmState.profile,tmState.a,tmState.b]);
 if(pair===tmChannelState.pair)return;
 tmChannelState.pair=pair;tmChannelState.generation++;tmChannelState.details=null;$('tm-channel-charts').textContent='';
 if($('tm-channel-box').open)tmChannelOpen();
}
async function tmChannelLoad() {
 const gen=++tmChannelState.generation, identity=tmChannelIdentity();
 const keys=tmChannelKeys(), axis=$('tm-channel-axis').value,start=$('tm-channel-start').value,end=$('tm-channel-end').value;
 const B=tmState.lapB,A=tmState.lapA;
 if(!B){tmChannelStatus('Select a recorded lap',true);return;}
 if(!keys.length||keys.length>8){tmChannelStatus('Select between one and eight channels',true);return;}
 tmChannelStatus('Loading selected channels…');
 const ask=l=>l?tmGet('/api/telemetry/channels?'+new URLSearchParams({rec:l.rec,session:l.session,lap:l.lap,keys:keys.join(','),axis,start,end})):Promise.resolve(null);
 let results;try{results=await Promise.all([ask(A),ask(B)]);}catch(_e){if(gen===tmChannelState.generation)tmChannelStatus('Could not load channel detail',true);return;}
 if(gen!==tmChannelState.generation||identity!==tmChannelIdentity())return;
 if(!results[1]?.ok||results[1].unindexed){tmChannelStatus(results[1]?.error||'Refresh to rebuild the recording index',true);return;}
 tmChannelState.details=results.map(r=>r?.ok?r.detail:null);tmChannelRender();
 const warning=results[0]&&!results[0].ok?' · Lap A unavailable for this window: '+results[0].error:'';
 tmChannelStatus(results[1].detail.time_basis+' · '+results[1].detail.distance_basis+warning);
}
function tmChannelRender() {
 const root=$('tm-channel-charts');root.textContent='';const details=tmChannelState.details;if(!details)return;
 const B=details[1],A=details[0], axis=B.axis==='time'?'t':'d';
 for(const channel of B.channels){
  const card=document.createElement('div'),caption=document.createElement('p');caption.className='sub';
  const available=B.availability[channel.key];
  caption.textContent=channel.label+' · '+channel.unit+' · '+channel.provenance+' · '+channel.offset+
   (available.constant?' · constant '+available.min:'')+' · '+Object.entries(available.states).map(([k,n])=>k+' '+n).join(', ');
  caption.title=channel.source+(channel.uncertainty?' · '+channel.uncertainty:'');card.append(caption);
  const svg=tmEl('svg',{class:'tm-channel-chart',role:'img','aria-label':channel.label+' over '+(axis==='t'?'raw lap time':'lap distance')});card.append(svg);
  const W=Math.max(300,Math.round(root.getBoundingClientRect().width)||760),H=130;
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  const sets=[A&&{rows:A.rows,cls:'tm-a'}, {rows:B.rows,cls:'tm-b'}].filter(Boolean);
  const finite=sets.flatMap(s=>s.rows.map(r=>r[channel.key])).filter(v=>v!=null&&Number.isFinite(v));
  const xs=sets.flatMap(s=>s.rows.map(r=>r[axis])).filter(v=>v!=null&&Number.isFinite(v));
  if(!finite.length||!xs.length){tmEl('text',{x:10,y:40},svg).textContent='No available values in this window';root.append(card);continue;}
  let lo=Math.min(...finite),hi=Math.max(...finite);if(lo===hi){lo-=.5;hi+=.5;}
  const xmin=Math.min(...xs),xmax=Math.max(...xs),X=v=>40+(v-xmin)/(xmax-xmin||1)*(W-50),Y=v=>105-(v-lo)/(hi-lo)*95;
  tmEl('rect',{x:40,y:10,width:W-50,height:95,class:'tm-frame'},svg);
  for(const set of sets){let path='',prior=null;
   for(const row of set.rows){const value=row[channel.key],x=row[axis];if(value==null||x==null){prior=null;continue;}
    const px=X(x).toFixed(2),py=Y(value).toFixed(2);
    path+=prior==null?'M'+px+' '+py:channel.type==='step'?'H'+px+'V'+py:'L'+px+' '+py;prior=value;}
   tmEl('path',{d:path,class:set.cls},svg);}
  tmEl('text',{x:2,y:18},svg).textContent=hi.toFixed(1);tmEl('text',{x:2,y:105},svg).textContent=lo.toFixed(1);
  tmEl('text',{x:40,y:125},svg).textContent=xmin.toFixed(2)+(axis==='t'?' s':' m');
  tmEl('text',{x:W-10,y:125,'text-anchor':'end'},svg).textContent=xmax.toFixed(2)+(axis==='t'?' s':' m');
  const read=document.createElement('p');read.className='sub';card.append(read);
  svg.onpointermove=e=>{const box=svg.getBoundingClientRect(),at=xmin+((e.clientX-box.left)*W/box.width-40)/(W-50)*(xmax-xmin);
   const row=B.rows.reduce((best,r,i)=>!best||Math.abs(r[axis]-at)<Math.abs(best.row[axis]-at)?{row:r,i}:best,null);
   if(!row)return;const value=row.row[channel.key],state=B.states[row.i][channel.key],raw=B.raw[row.i][channel.key];
   read.textContent=row.row[axis].toFixed(3)+(axis==='t'?' s':' m')+' · B '+(value==null?state:value.toFixed(4)+' '+channel.unit)+(channel.diagnostic?' · raw '+(raw==null?'missing':'0x'+raw):'');};
  root.append(card);
 }
}

// Shared world-coordinate track editor. Draft changes apply only through the explicit button.
const tmEditor = {open:false,generation:0,reply:null,draft:null,mode:'apex',corner:0,boundary:1};
function tmEditorStatus(text,error=false){$('tm-editor-status').textContent=text;$('tm-editor-status').className=error?'enverr':'sub';}
function tmEditorClone(v){return JSON.parse(JSON.stringify(v));}
const tmReferenceLayouts={generation:0,rows:[]};
function tmReferenceLayoutsReset(){
 tmReferenceLayouts.generation++;tmReferenceLayouts.rows=[];
 $('tm-reference-layouts').open=false;$('tm-reference-layout').textContent='';$('tm-reference-edit').disabled=true;
}
async function tmReferenceLayoutsLoad(){
 if(!$('tm-reference-layouts').open)return;
 const gen=++tmReferenceLayouts.generation,profile=tmState.profile;
 $('tm-reference-edit').disabled=true;$('tm-reference-status').textContent='Reading downloaded layouts…';
 const d=await tmGet('/api/telemetry/definition-layouts');
 if(gen!==tmReferenceLayouts.generation||profile!==tmState.profile)return;
 const select=$('tm-reference-layout');select.textContent='';tmReferenceLayouts.rows=d.ok?d.tracks:[];
 for(const [i,row] of tmReferenceLayouts.rows.entries()){const option=document.createElement('option');option.value=String(i);option.textContent=row.track+' · '+row.layout+' · '+(row.reverse?'reverse':'forward');select.append(option);}
 $('tm-reference-edit').disabled=!tmReferenceLayouts.rows.length;
 $('tm-reference-status').textContent=!d.ok?(d.error||'Could not read layouts'):tmReferenceLayouts.rows.length?'Downloaded reference lines supply unreviewed proposals. Review markers and sectors before applying.':'No downloaded reference lines. Run racecast gt7-data update, then refresh layouts.';
}
function tmEditorOpenLayout(){const row=tmReferenceLayouts.rows[Number($('tm-reference-layout').value)];if(row)tmEditorOpen({layout_id:row.id,reverse:row.reverse});}
async function tmEditorOpen(layout=null){
 const B=tmState.lapB;if(!layout&&!B)return;
 const gen=++tmEditor.generation,key=B?tmKey(B):null,profile=tmState.profile;
 tmEditor.open=true;tmEditor.reply=null;tmEditor.draft=null;$('tm-editor-fields').textContent='';$('tm-editor-map').textContent='';$('tm-editor-modal').hidden=false;const owner=tmEditorLock();tmEditorStatus('Loading shared track definition…');
 let d;try{d=await tmGet('/api/telemetry/definition?'+new URLSearchParams(layout||{rec:B.rec,session:B.session,lap:B.lap}));}catch(_e){d={ok:false,error:'Could not load the shared definition'};}finally{tmEditorRelease(owner);}
 if(gen!==tmEditor.generation||!tmEditor.open||(!layout&&(!tmState.lapB||tmKey(tmState.lapB)!==key))||tmState.profile!==profile)return;
 if(!d.ok){tmEditorStatus(d.error||'Track definition unavailable',true);return;}
 tmEditor.reply=d;tmEditor.draft=tmEditorClone(d.definition);tmEditor.corner=0;tmEditor.boundary=1;tmEditorRender();
 if(d.projection_error)tmEditorStatus('Cannot apply yet: '+d.projection_error+'. Move the anchor on the map, then Apply.',true);
 else tmEditorStatus('Shared on this machine. Draft edits do not change analysis until Apply.');
}
function tmEditorRelease(owner=tmEditor.controls){if(!owner||owner!==tmEditor.controls)return;for(const [element,disabled] of owner)element.disabled=disabled;tmEditor.controls=null;tmEditor.busy=false;}
function tmEditorLock(){tmEditorRelease();const owner=[...$('tm-editor-modal').querySelectorAll('button,input,select,textarea')].filter(e=>e.id!=='tm-editor-close').map(e=>[e,e.disabled]);tmEditor.controls=owner;tmEditor.busy=true;for(const [e] of owner)e.disabled=true;return owner;}
function tmEditorClose(){tmEditorRelease();tmEditor.open=false;tmEditor.generation++;$('tm-editor-modal').hidden=true;}
function tmEditorField(parent,label,value,setter,numeric=false){
 const wrap=document.createElement('label');wrap.className='tcfield';const title=document.createElement('span');title.textContent=label;
 const input=document.createElement(label==='Provenance notes'?'textarea':'input');if(label==='Provenance notes')input.rows=3;else input.type='text';input.value=value??'';input.setAttribute('aria-label',label);
 input.oninput=()=>{setter(numeric?(input.value.trim()===''?null:Number(input.value)):input.value);tmEditorMap();};wrap.append(title,input);parent.append(wrap);return input;
}
function tmEditorSelect(parent,label,value,options,setter){
 const wrap=document.createElement('label');wrap.className='tcfield';const title=document.createElement('span');title.textContent=label;
 const select=document.createElement('select');select.setAttribute('aria-label',label);
 for(const [v,t] of options){const option=document.createElement('option');option.value=v;option.textContent=t;select.append(option);}
 select.value=value;select.onchange=()=>{setter(select.value);tmEditorRender();};wrap.append(title,select);parent.append(wrap);return select;
}
function tmEditorRender(){
 const root=$('tm-editor-fields');root.textContent='';const d=tmEditor.draft;if(!d)return;
 $('tm-editor-reviewed').checked=d.reviewed;$('tm-editor-info').textContent=d.layout_id+' · '+(d.reverse?'reverse':'forward')+' · '+tmEditor.reply.source+' · version '+d.version;
 tmEditorField(root,'Numbering scheme',d.numbering_scheme,v=>d.numbering_scheme=v);
 tmEditorField(root,'Provenance notes',d.provenance.join('\n'),v=>d.provenance=v.split('\n'));
 tmEditorSelect(root,'Selected corner',String(tmEditor.corner),d.corners.map((c,i)=>[String(i),'T'+c.number+(c.name?' · '+c.name:'')]),v=>tmEditor.corner=Number(v));
 const corner=d.corners[tmEditor.corner];
 if(corner){tmEditorField(root,'Corner number',corner.number,v=>corner.number=v,true);tmEditorField(root,'Corner name',corner.name,v=>corner.name=v);
  tmEditorSelect(root,'Corner direction',corner.direction,[['left','Left'],['right','Right'],['unknown','Unknown']],v=>corner.direction=v);
  tmEditorSelect(root,'Map edit target',tmEditor.mode,[['entry','Corner entry'],['apex','Corner apex'],['exit','Corner exit'],['boundary','Sector boundary']],v=>tmEditor.mode=v);
  for(const key of ['entry','apex','exit'])for(const axis of ['x','z'])tmEditorField(root,key+' '+axis.toUpperCase(),corner[key][axis],v=>corner[key][axis]=v,true);
 }
 tmEditorSelect(root,'Default sector variant',d.default_variant,d.variants.map(v=>[v.id,v.name]),v=>d.default_variant=v);
 const variant=d.variants.find(v=>v.id===d.default_variant);if(variant){
  tmEditorField(root,'Variant name',variant.name,v=>variant.name=v);
  tmEditorSelect(root,'Sector kind',variant.kind,[['coaching','Coaching sections'],['game','GT7 game splits'],['provisional','Provisional division']],v=>{variant.kind=v;if(v==='provisional')variant.reviewed=false;});
  tmEditorSelect(root,'Sector boundaries reviewed',String(variant.reviewed),[['false','Unreviewed'],['true','Reviewed']],v=>variant.reviewed=v==='true');
  tmEditorField(root,'Verification evidence (required for reviewed GT7 splits)',variant.evidence||'',v=>variant.evidence=v);
  tmEditorSelect(root,'Selected sector boundary',String(tmEditor.boundary),variant.boundaries.map((_p,i)=>[String(i),i===0?'Start/finish':i===variant.boundaries.length-1?'Closing start/finish':'End of sector '+i]),v=>{tmEditor.boundary=Number(v);tmEditor.mode='boundary';});
  const point=variant.boundaries[tmEditor.boundary]||variant.boundaries[0];
  for(const axis of ['x','z'])tmEditorField(root,'Boundary '+axis.toUpperCase(),point[axis],v=>point[axis]=v,true);
  variant.sector_names.forEach((name,i)=>tmEditorField(root,'Sector '+(i+1)+' name',name,v=>variant.sector_names[i]=v));
 }
 (d.combinations||[]).forEach(combination=>{
  tmEditorField(root,'Combination name',combination.name,v=>combination.name=v);
  const wrap=document.createElement('label');wrap.className='tcfield';wrap.append(document.createTextNode('Connected corners'));
  const select=document.createElement('select');select.multiple=true;select.size=4;select.setAttribute('aria-label','Connected corners');
  for(const corner of d.corners){const option=document.createElement('option');option.value=corner.id;option.textContent='T'+corner.number+(corner.name?' '+corner.name:'');option.selected=combination.corner_ids.includes(corner.id);select.append(option);}
  select.onchange=()=>combination.corner_ids=[...select.selectedOptions].map(o=>o.value);wrap.append(select);root.append(wrap);
 });
 tmEditorMap();
}
function tmEditorMap(){
 const svg=$('tm-editor-map');svg.textContent='';const d=tmEditor.draft,geometry=tmEditor.reply?.geometry||[];if(!d||!geometry.length)return;
 const W=520,H=430,xs=geometry.map(p=>p.x),zs=geometry.map(p=>p.z);
 const xmin=Math.min(...xs),xmax=Math.max(...xs),zmin=Math.min(...zs),zmax=Math.max(...zs);
 const scale=Math.min((W-40)/(xmax-xmin||1),(H-40)/(zmax-zmin||1)),P=p=>[20+(p.x-xmin)*scale,20+(p.z-zmin)*scale];
 svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.style.width='100%';
 tmEl('path',{d:geometry.map((p,i)=>{const [x,y]=P(p);return(i?'L':'M')+x+' '+y;}).join(''),class:'tm-a'},svg);
 const dot=(point,label,cls)=>{if(!point||!Number.isFinite(point.x)||!Number.isFinite(point.z))return;
  const [x,y]=P(point);tmEl('circle',{cx:x,cy:y,r:4,class:cls},svg);
  tmEl('text',{x:x+6,y:y-5},svg).textContent=label;};
 d.corners.forEach((corner,i)=>{for(const key of ['entry','apex','exit'])dot(corner[key],key==='apex'?'T'+corner.number:'',i===tmEditor.corner?'tm-marker-selected':'tm-marker');});
 const variant=d.variants.find(v=>v.id===d.default_variant);variant?.boundaries.slice(0,-1).forEach((p,i)=>dot(p,i===0?'S/F':'S'+i,'tm-boundary'));
 svg.onpointerdown=e=>{if(tmEditor.busy)return;const box=svg.getBoundingClientRect(),x=(e.clientX-box.left)*W/box.width,y=(e.clientY-box.top)*H/box.height;
  const point={x:Math.round((xmin+(x-20)/scale)*10)/10,z:Math.round((zmin+(y-20)/scale)*10)/10};
  if(tmEditor.mode==='boundary'&&variant){variant.boundaries[tmEditor.boundary]=point;
   if(tmEditor.boundary===0)variant.boundaries[variant.boundaries.length-1]=tmEditorClone(point);
   if(tmEditor.boundary===variant.boundaries.length-1)variant.boundaries[0]=tmEditorClone(point);
  }else if(d.corners[tmEditor.corner])d.corners[tmEditor.corner][tmEditor.mode]=point;
  tmEditorRender();tmEditorStatus('Draft position changed. Apply to update shared analysis.');};
}
function tmEditorAddCorner(){
 const g=tmEditor.reply?.geometry||[];if(!g.length)return;
 const at=f=>tmEditorClone(g[Math.min(g.length-1,Math.round((g.length-1)*f))]);
 const point=f=>{const p=at(f);return{x:p.x,z:p.z};},d=tmEditor.draft;
 d.corners.push({id:'corner-'+Date.now().toString(36)+Math.random().toString(36).slice(2,8),
  number:Math.max(0,...d.corners.map(c=>c.number))+1,name:'',direction:'unknown',entry:point(.15),apex:point(.2),exit:point(.25)});
 tmEditor.corner=d.corners.length-1;d.reviewed=false;tmEditorRender();
}
function tmEditorRemoveCorner(){if(!tmEditor.draft)return;const d=tmEditor.draft,id=d.corners[tmEditor.corner]?.id;d.combinations=(d.combinations||[]).map(c=>({...c,corner_ids:c.corner_ids.filter(v=>v!==id)})).filter(c=>c.corner_ids.length);tmEditor.draft.corners.splice(tmEditor.corner,1);tmEditor.corner=Math.max(0,tmEditor.corner-1);tmEditorRender();}
function tmEditorAddVariant(){
 const d=tmEditor.draft;if(!d)return;const source=d.variants.find(v=>v.id===d.default_variant);if(!source)return;
 const variant=tmEditorClone(source);variant.id='variant-'+Date.now().toString(36);variant.name='New coaching division';variant.kind='coaching';variant.reviewed=false;
 d.variants.push(variant);d.default_variant=variant.id;tmEditor.boundary=1;tmEditorRender();
}
function tmEditorAddBoundary(){
 if(!tmEditor.draft)return;const v=tmEditor.draft.variants.find(v=>v.id===tmEditor.draft.default_variant);if(!v)return;
 const i=Math.max(1,Math.min(v.boundaries.length-1,tmEditor.boundary)),a=v.boundaries[i-1],b=v.boundaries[i];
 const geometry=tmEditor.reply.geometry;
 const nearest=p=>geometry.reduce((best,r)=>!best||(r.x-p.x)**2+(r.z-p.z)**2<(best.x-p.x)**2+(best.z-p.z)**2?r:best,null);
 const da=i===1?0:nearest(a).d,db=i===v.boundaries.length-1?geometry.at(-1).d:nearest(b).d;
 const middle=geometry.reduce((best,r)=>!best||Math.abs(r.d-(da+db)/2)<Math.abs(best.d-(da+db)/2)?r:best,null);
 v.boundaries.splice(i,0,{x:middle.x,z:middle.z});v.sector_names.splice(i-1,0,'New section');v.reviewed=false;tmEditor.boundary=i;tmEditor.mode='boundary';tmEditorRender();
}
function tmEditorRemoveBoundary(){
 if(!tmEditor.draft)return;const v=tmEditor.draft.variants.find(v=>v.id===tmEditor.draft.default_variant),i=tmEditor.boundary;
 if(!v||i<=0||i>=v.boundaries.length-1)return;
 v.boundaries.splice(i,1);v.sector_names.splice(i-1,1);v.reviewed=false;tmEditor.boundary=Math.min(i,v.boundaries.length-1);tmEditorRender();
}
function tmEditorAddCombination(){if(!tmEditor.draft||!tmEditor.draft.corners.length)return;
 (tmEditor.draft.combinations||=[]).push({id:'combination-'+Date.now().toString(36),name:'Connected sequence',corner_ids:[tmEditor.draft.corners[tmEditor.corner].id]});tmEditorRender();}
async function tmEditorApply(){
 const d=tmEditor.reply;if(!d||!tmEditor.draft)return;
 const gen=tmEditor.generation, draft=tmEditorClone(tmEditor.draft);
 const payload={scope:d.scope,layout_id:d.layout_id,reverse:d.reverse,rec:d.rec,session:d.session,lap:d.lap,profile:d.profile,source_id:d.source_id,expected_revision:d.revision,definition:draft};
 const owner=tmEditorLock();tmEditorStatus('Applying shared definition…');
 let answer;try{const r=await fetch('/api/telemetry/definition',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});answer=await r.json();}
 catch(_e){if(gen===tmEditor.generation)tmEditorStatus('Could not apply; draft remains in this editor',true);return;}
 finally{tmEditorRelease(owner);}
 if(gen!==tmEditor.generation||!tmEditor.open)return;
 if(!answer.ok){tmEditorStatus(answer.error||'Could not apply definition',true);return;}
 tmEditor.reply=answer;tmEditor.draft=tmEditorClone(answer.definition);tmEditorRender();tmEditorStatus('Applied shared definition · revision '+answer.revision);
 if(d.rec)await tmRefreshContext(d.rec);
 else if(tmState.lapB)await tmRefreshContext(tmState.lapB.rec);
}
async function tmSelectSectorVariant(value){
 const B=tmState.lapB;if(!B)return;
 const key=tmKey(B),profile=tmState.profile;
 const context=await tmGet('/api/telemetry/context?'+new URLSearchParams({rec:B.rec}));
 if(!context.ok||!tmState.lapB||tmKey(tmState.lapB)!==key||tmState.profile!==profile)return;
 const payload={rec:B.rec,profile:context.profile,source_id:context.context.source_id,
  expected_revision:context.context.revision,action:'select-track-variant',session:B.session,lap:B.lap,variant_id:value};
 const r=await fetch('/api/telemetry/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),answer=await r.json();
 if(!tmState.lapB||tmKey(tmState.lapB)!==key||tmState.profile!==profile)return;
 if(!answer.ok){$('tm-large-status').textContent=answer.error||'Could not select sector variant';return;}
 await tmRefreshContext(B.rec);
}
function tmRenderLargerSectors(){
 const root=$('tm-larger-sectors');root.textContent='';const B=tmState.lapB,A=tmState.lapA,select=$('tm-sector-variant');select.textContent='';
 const definition=B?.track_definition,larger=B?.larger_sectors;
 $('tm-large-status').textContent=(B?.track_definition_warnings||[]).join(' · ');
 if(!definition||!larger){$('tm-large-status').textContent||='No reviewed marker/sector definition for this layout';return;}
 for(const variant of definition.variants){const option=document.createElement('option');option.value=variant.id;
  option.textContent=variant.name+' · '+variant.kind+(variant.reviewed?'':' · unreviewed');select.append(option);}
 select.value=larger.variant_id;
 $('tm-large-status').textContent=[definition.reviewed?'Reviewed definition':'Unreviewed proposals','v'+definition.version,
  definition.numbering_scheme,definition.catalogue_turns!=null&&definition.corners.length!==definition.catalogue_turns?'Catalogue '+definition.catalogue_turns+' / this scheme '+definition.corners.length:'',...(B.track_definition_warnings||[])].filter(Boolean).join(' · ');
 const compatible=A?.definition_fingerprint===B.definition_fingerprint&&A?.sector_variant_id===B.sector_variant_id;
 larger.names.forEach((name,i)=>{const row=document.createElement('p');const b=larger.times_s[i],a=compatible?A?.larger_sectors?.times_s[i]:null;
  row.textContent=name+' · '+(larger.bounds_m[i]/1000).toFixed(2)+'–'+(larger.bounds_m[i+1]/1000).toFixed(2)+' km · B '+tmTime(b)+(a!=null&&b!=null?' · A '+tmTime(a)+' · '+tmSigned(b-a)+' s':'');root.append(row);});
 const theory=tmState.pool?.sector_theory;
 if(theory?.variant_id===larger.variant_id){
  for(const sector of theory.best_sectors||[]){const row=document.createElement('p'),source=sector.source||{};
   row.className='sub';row.textContent=sector.name+' best '+tmTime(sector.time_s)+' · '+(theory.confirmed?'confirmed':'unconfirmed')+' · '+(source.rec||'unknown recording')+' · session '+source.session+' · lap '+source.lap+' · '+(source.compound||'unknown compound');root.append(row);}
 }
 const metrics=document.createElement('details'),label=document.createElement('summary');label.textContent='Corner / combination measurements';metrics.append(label);
 for(const corner of B.corner_metrics||[]){const text=document.createElement('p');text.className='sub';text.textContent='T'+corner.number+(corner.name?' '+corner.name:'')+' · '+corner.direction+' · '+tmTime(corner.time_s)+' · entry / anchor / exit '+[corner.entry_speed_kmh,corner.anchor_speed_kmh,corner.exit_speed_kmh].map(v=>v==null?'unknown':v.toFixed(1)).join(' / ')+' km/h · min '+(corner.min_speed_kmh==null?'unknown':corner.min_speed_kmh.toFixed(1))+' km/h';metrics.append(text);}
 for(const combination of B.combination_metrics||[]){const text=document.createElement('p');text.className='sub';text.textContent=combination.name+' · '+combination.numbers.map(n=>'T'+n).join(' / ')+' · '+tmTime(combination.time_s);metrics.append(text);}
 root.append(metrics);
}

if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('keydown',e=>{
 if(!tmEditor.open)return;if(e.key==='Escape'){e.preventDefault();tmEditorClose();}
 if(e.key==='Tab'){const fields=[...$('tm-editor-modal').querySelectorAll('button,input,select,textarea')].filter(f=>!f.disabled&&f.offsetParent!==null);
 if(fields.length&&e.shiftKey&&document.activeElement===fields[0]){e.preventDefault();fields.at(-1).focus();}
 else if(fields.length&&!e.shiftKey&&document.activeElement===fields.at(-1)){e.preventDefault();fields[0].focus();}}
});


// Vehicle-specific post-session lookup. Ordinary reads remain offline.
const tmShiftState={analysis:null,generation:0,pair:null,updating:null};
function tmShiftStatus(text,error=false){$('tm-shift-status').textContent=text;$('tm-shift-status').className=error?'enverr':'sub';}
function tmShiftIdentity(){return JSON.stringify([tmState.profile,tmState.lapB&&tmKey(tmState.lapB)]);}
function tmShiftRefresh(){
 const pair=tmShiftIdentity();if(pair===tmShiftState.pair)return;
 tmShiftState.pair=pair;tmShiftState.generation++;tmShiftState.analysis=null;$('tm-shift-values').textContent='';
 if($('tm-shift-box').open)tmShiftLoad();
}
async function tmShiftLoad(){
 if(!$('tm-shift-box').open)return;
 const B=tmState.lapB;if(!B){tmShiftStatus('Select a recorded lap');return;}
 const identity=tmShiftIdentity(),gen=++tmShiftState.generation;
 tmShiftState.analysis=null;$('tm-shift-values').textContent='';tmShiftStatus('Reading local reference and original shift phases…');
 let answer;try{answer=await tmGet('/api/telemetry/shifts?'+new URLSearchParams({rec:B.rec,session:B.session,lap:B.lap}));}
 catch(_e){answer={ok:false,error:'Could not load shift observations'};}
 if(gen!==tmShiftState.generation||identity!==tmShiftIdentity())return;
 if(!answer.ok){tmShiftStatus(answer.error||'Shift lookup unavailable',true);return;}
 tmShiftState.analysis=answer.analysis;tmShiftRender();tmShiftStatus((answer.analysis.warnings||[]).join(' · ')||answer.analysis.time_basis||'Local reference');
}
// Vehicle shift references by car, independent of a recording (Data management).
const tmShiftRef={loaded:false,generation:0,busy:new Set()};
function tmShiftRefStatus(text,error=false){$('tm-shiftref-status').textContent=text;$('tm-shiftref-status').className=error?'enverr':'sub';}
async function tmShiftRefSearch(){
 const q=$('tm-shiftref-q').value.trim(),gen=++tmShiftRef.generation;tmShiftRef.loaded=true;
 tmShiftRefStatus(q?'Searching…':'Reading local entries…');
 const d=await tmGet('/api/telemetry/shift-references?'+new URLSearchParams({q}));
 if(gen!==tmShiftRef.generation)return;
 if(!d.ok){tmShiftRefStatus(d.error||'Shift references unavailable',true);return;}
 tmShiftRefRender(d.cars||[]);
 tmShiftRefStatus(d.cars?.length?(q?d.cars.length+' matching car'+(d.cars.length===1?'':'s')+(d.cars.length>=25?' (first 25)':''):d.cars.length+' local entr'+(d.cars.length===1?'y':'ies')):(q?'No matching car':'No local entries yet. Search a car to fetch its reference.'));
}
function tmShiftRefRender(cars){
 const root=$('tm-shiftref-list');root.textContent='';if(!cars.length)return;
 const wrap=document.createElement('div');wrap.className='tmsecwrap';const table=document.createElement('table');table.className='prodtable tmsec';
 const head=table.createTHead().insertRow();for(const h of ['Car','ID','Local entry','Power curve','Gear ratios',''])head.append(Object.assign(document.createElement('th'),{textContent:h}));
 const body=table.createTBody();
 for(const car of cars){const row=body.insertRow();
  row.insertCell().textContent=[car.maker,car.name].filter(Boolean).join(' ');row.insertCell().textContent=String(car.id);
  row.insertCell().textContent=car.error||(car.cached?'Updated '+(car.fetched_at?new Date(car.fetched_at).toLocaleString():'(time unknown)'):'None');
  row.insertCell().textContent=car.cached?(car.curve?'Yes':'Not in source'):'—';row.insertCell().textContent=car.cached?(car.ratios?'Yes':'Not in source'):'—';
  const button=document.createElement('button');button.textContent=car.cached?'Refresh':'Fetch reference';button.disabled=tmShiftRef.busy.has(car.id);
  button.onclick=()=>tmShiftRefUpdate(car,button);row.insertCell().append(button);}
 wrap.append(table);root.append(wrap);
}
async function tmShiftRefUpdate(car,button){
 if(tmShiftRef.busy.has(car.id))return;tmShiftRef.busy.add(car.id);button.disabled=true;
 const label=[car.maker,car.name].filter(Boolean).join(' ')||'Car '+car.id;tmShiftRefStatus('Fetching the reference for '+label+'…');
 try{const job=await startJob('shift-reference-update',{car_id:car.id}),answer=job.error?job:await jobResult(job.job_id,60);
  if(answer.error||answer.code!==0){tmShiftRefStatus(answer.error||(answer.lines||[]).join(' · ')||'Update failed; previous entry retained',true);return;}
  await tmShiftRefSearch();tmShiftRefStatus('Updated '+label+'.');
  if(tmState.lapB?.car_id===car.id&&$('tm-shift-box')?.open)tmShiftLoad();
 }catch(_e){tmShiftRefStatus('Update failed; previous entry retained',true);}
 finally{tmShiftRef.busy.delete(car.id);button.disabled=false;}
}
function tmShiftTable(root,headers,rows){
 const wrap=document.createElement('div');wrap.className='tmsecwrap';const table=document.createElement('table');table.className='prodtable tmsec';
 const head=table.createTHead().insertRow();headers.forEach(h=>{const th=document.createElement('th');th.textContent=h;head.append(th);});
 const body=table.createTBody();for(const values of rows){const row=body.insertRow();for(const value of values)row.insertCell().textContent=value==null?'unavailable':String(value);}
 wrap.append(table);root.append(wrap);
}
function tmShiftRender(){
 const root=$('tm-shift-values');root.textContent='';const analysis=tmShiftState.analysis;if(!analysis)return;
 const ref=analysis.reference||{},say=(text,parent=root)=>{const p=document.createElement('p');p.className='sub';p.textContent=text;parent.append(p);};
 say('Car ID '+ref.car_id+' · '+ref.kind+' · '+(ref.event_confirmed?'Event-confirmed table':'Event applicability unverified')+(ref.configuration?' · '+ref.configuration:''));
 say('Full-throttle, sufficient-grip acceleration reference. Earlier shifts may save fuel, manage traction or prepare braking. Deviations are neutral; power curves do not establish optimum fuel-saving targets.');
 const metadata=document.createElement('details'),metaTitle=document.createElement('summary');metaTitle.textContent='Reference source & applicability';metadata.append(metaTitle);root.append(metadata);
 const source=ref.source||{};if(source.identity)say(source.identity+' · commit '+source.commit+' · fetched '+source.fetched_at+' · '+(ref.algorithm||'manual event table'),metadata);
 if(source.hashes)say('Source SHA-256 · '+JSON.stringify(source.hashes),metadata);
 if(ref.provenance)say(ref.provenance,metadata);
 const show=v=>v==null?'unavailable':JSON.stringify(v);
 say('Source ratios '+show(ref.source_ratios)+' · decoded gearbox '+show(ref.observed_ratios)+' · reference ratio source '+(ref.ratio_source||'manual table')+' · matching ratios '+show(ref.ratios_match),metadata);
 say('Curve RPM range '+show(ref.curve_range_rpm)+' · source revbar indication '+show(ref.revbar_rpm)+' · recorded display alerts '+show(analysis.display_alerts)+' · separately confirmed rev limit '+show(ref.rev_limit_rpm),metadata);
 if(ref.support)say('Availability · '+Object.entries(ref.support).map(([k,v])=>k.replaceAll('_',' ')+': '+(v?'available':'unavailable')).join(' · '),metadata);
 for(const warning of ref.warnings||[])say(warning,metadata);
 tmShiftTable(root,['Outgoing gear','Acceleration reference RPM','Availability'],(ref.targets||[]).map(t=>[t.gear,t.rpm==null?null:Math.round(t.rpm*10)/10,t.status]));
 if(ref.curve?.length){
  say(ref.curve_units||'Power and torque each normalized to maximum 100; no physical HP/kW/Nm');
  const svg=tmEl('svg',{viewBox:'0 0 760 160',role:'img','aria-label':'Normalized source power and torque versus RPM',class:'tm-channel-chart'},root);
  const lo=ref.curve[0].rpm,hi=ref.curve.at(-1).rpm,X=r=>50+(r-lo)/(hi-lo||1)*680,Y=v=>130-v;
  tmEl('rect',{x:50,y:30,width:680,height:100,class:'tm-frame'},svg);
  for(const [key,cls] of [['power','tm-b'],['torque','tm-a']])tmEl('path',{d:ref.curve.map((p,i)=>(i?'L':'M')+X(p.rpm)+' '+Y(p[key])).join(''),class:cls},svg);
  tmEl('text',{x:4,y:36},svg).textContent='100%';tmEl('text',{x:15,y:130},svg).textContent='0%';
  tmEl('text',{x:50,y:150},svg).textContent=lo+' RPM';tmEl('text',{x:650,y:150},svg).textContent=hi+' RPM';
  say('Blue: normalized power · amber: normalized torque. A confirmed manual target table does not confirm this external curve shape.');
  const detail=document.createElement('details'),title=document.createElement('summary');title.textContent='All normalized source values';detail.append(title);root.append(detail);
  tmShiftTable(detail,['RPM','Power (%)','Torque (%)'],ref.curve.map(p=>[p.rpm,p.power,p.torque]));
 }
 const signed=v=>v==null?null:(v>0?'+':'')+Math.round(v);
 tmShiftTable(root,['Shift','Pre-cut RPM','Post RPM','Acceleration target','Acceleration delta','Economy target','Economy delta','Stint / compound','Phase'],
  (analysis.shifts||[]).map(s=>[s.from_gear+' → '+s.to_gear,s.pre_cut_rpm,s.post_rpm,s.acceleration_target_rpm,signed(s.acceleration_deviation_rpm),
   s.economy_target_rpm,signed(s.economy_deviation_rpm),[s.stint_id,s.compound].filter(Boolean).join(' / '),s.phase_status]));
 say('Pre-cut is inferred from stable drive/cut phases in original packets; the last old-gear packet may already be cut. Missing or ambiguous phases remain unavailable. Post RPM uses an engaged new-gear bracket.');
 $('tm-shift-copy').disabled=ref.kind!=='external'||!(ref.targets||[]).some(t=>t.rpm!=null);
}
async function tmShiftUpdate(){
 const B=tmState.lapB;if(!B||!Number.isInteger(B.car_id)||tmShiftState.updating)return;
 const identity=tmShiftIdentity(),owner={};tmShiftState.updating=owner;const button=$('tm-shift-update');button.disabled=true;
 tmShiftStatus('Explicitly updating this vehicle from the source…');
 try{const job=await startJob('shift-reference-update',{car_id:B.car_id}),answer=job.error?job:await jobResult(job.job_id,60);
  if(identity!==tmShiftIdentity())return;
  if(answer.error||answer.code!==0){tmShiftStatus(answer.error||(answer.lines||[]).join(' · ')||'Update failed; previous cache retained',true);return;}
  await tmShiftLoad();
 }catch(_e){if(identity===tmShiftIdentity())tmShiftStatus('Update failed; previous cache retained',true);}
 finally{if(tmShiftState.updating===owner){tmShiftState.updating=null;button.disabled=false;}}
}
async function tmShiftCopy(){
 const B=tmState.lapB,ref=tmShiftState.analysis?.reference;if(!B||ref?.kind!=='external')return;
 const identity=tmShiftIdentity(),targets=Object.fromEntries((ref.targets||[]).filter(t=>t.rpm!=null).map(t=>[String(t.gear),t.rpm]));
 await tcOpen();if(identity!==tmShiftIdentity()||tc.target!==B.rec)return;
 tc.session=String(B.session);tcSession().shift_reference={car_id:B.car_id,targets,configuration:'',confirmed:false,
  provenance:'Copied acceleration calculation '+ref.algorithm+' · '+ref.source?.identity+' · commit '+ref.source?.commit+' · '+JSON.stringify(ref.source?.hashes)};
 tcRender();tcChanged();tcStatus('Copied as unconfirmed event table. Enter intended configuration and confirm applicability separately.');
}

#!/usr/bin/env python3
"""Control Center AI analysis: the status poll and the displayed report. The runner keeps a
finished run's terminal state in the active record until its cleanup ends; the page must
show that run's own result, not the previous report under a terminal status (#885).
The JS checks run under node when it is installed (skipped otherwise).
Run: python3 tests/test_ai_status_ui.py"""
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "src", "ui", "ai-analysis.js")


def _src():
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


def _func_src(src, name):
    """A function: its first line plus the indented lines and its closing brace."""
    marker = f"\nasync function {name}(" if f"\nasync function {name}(" in src else f"\nfunction {name}("
    i = src.index(marker) + 1
    lines = src[i:].split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if not line.startswith(" "):
            if line.startswith("}"):
                out.append(line)
            break
        out.append(line)
    return "\n".join(out)


def _const_src(src, name):
    i = src.index(f"\nconst {name}=") + 1
    return src[i:src.index("\n", i)]


def _run_js(src):
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    return subprocess.run([node, "-"], input=src, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True, timeout=30).stdout


_HARNESS = r"""
class El {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; this.disabled = false; this.dataset = {}; }
  set textContent(v) { this._text = v; this.children = []; }
  get textContent() { return this._text; }
  append(c) { this.children.push(c); }
  setAttribute(k, v) { this[k] = v; }
  count(tag) { return this.children.reduce((n, c) => n + (c.tag === tag) + c.count(tag), 0); }
  scrollIntoView() {}
}
const els = {}; const $id = id => els[id] || (els[id] = new El('div'));
const document = {createElement: tag => new El(tag)};
const node = (tag, text, parent) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (parent) parent.append(e); return e; };
const say = (id, text) => { $id(id).textContent = text || ''; };
const error = doc => doc?.error?.message || 'Analysis operation failed';
const state = {generation: 0, job: null, lastJob: null};
const replies = {}, opened = [], requests = [];
const request = async (op, payload) => { requests.push(op); if (op === 'job') opened.push(payload.query.id);
  return op === 'job' ? replies.job[payload.query.id] : replies[op]; };
const history = async () => {}, invalidate = () => {}, prepareNewRun = () => {}, tmOpenReference = async () => true;
const run = (id, st, report) => ({ok: true, id, state: st, profile: 'a', requested_model: 'm',
  progress: st, error: st === 'failed' ? {message: 'Analysis execution failed'} : null, report});
const REPORT = {language: 'en', limitations: [], exercises: [],
  findings: [{priority: 1, title: 'Brake later', evidence: [], telemetry: {}, location_m: 500}]};
"""


def _harness():
    s = _src()
    consts = "".join(_const_src(s, n) + "\n" for n in ("FINISHED",) if f"\nconst {n}=" in s)
    return _HARNESS + consts + '\n'.join(_func_src(s, name) for name in ('feedback', 'runFeedback', 'disclosure', 'openRun', 'status')) + '\n'


def t_a_finished_run_replaces_the_previous_report():
    out = _run_js(_harness() + """
(async () => {
  replies.job = {X: run('X', 'completed', REPORT), Y: run('Y', 'failed', null)};
  replies.status = {ok: true, active: null, busy: false};
  state.job = 'X'; await status();                       // the earlier, completed run is shown
  const before = $id('ai-report').count('article');
  state.job = 'Y';                                       // a new run fails; cleanup still runs
  replies.status = {ok: true, busy: true, active: {...run('Y', 'failed', null), directory: 'd'}};
  await status();
  console.log([before, $id('ai-report').count('article'), $id('ai-feedback').dataset.kind,
               opened.join(',')].join('|'));
})();""")
    if out is not None:
        assert out.strip() == "1|0|failed|X,Y", out


def t_a_running_or_foreign_run_still_only_reports_progress():
    out = _run_js(_harness() + """
(async () => {
  replies.job = {};
  state.job = 'Y';
  replies.status = {ok: true, busy: true, active: {id: 'Y', state: 'running', progress: 'Provider running'}};
  await status(); const running = $id('ai-feedback-title').textContent;
  replies.status = {ok: true, busy: true, active: {id: 'Z', state: 'failed', foreign: true}};
  await status(); const foreign = $id('ai-run-status').textContent;
  console.log([running, foreign.startsWith('An analysis is running in another profile'), opened.length].join('|'));
})();""")
    if out is not None:
        assert out.strip() == "Analysing selected laps|true|0", out


def t_model_suggestions_work_before_probe_and_do_not_choose_a_model():
    src = _src()
    function = src[src.index('function modelSuggestions(){'):src.index('\nfunction settingsStatus(')]
    out = _run_js(_HARNESS + function + r"""
const option = (select, value) => select.append({value});
state.models = {};
modelSuggestions(); // Settings have not loaded yet.
state.settings = {agents: [{id: 'claude', provider: 'claude'}, {id: 'codex', provider: 'codex'}]};
$id('ai-agent').value = 'claude';
$id('ai-model').value = 'pinned-model';
modelSuggestions();
console.log($id('ai-run-model-suggestions').children.map(o => o.value).join(','));
console.log($id('ai-model').value);
state.models.codex = {provider: 'codex', models: ['catalogue-model']};
$id('ai-agent').value = 'codex';
modelSuggestions();
console.log($id('ai-run-model-suggestions').children.map(o => o.value).join(','));
""")
    if out is not None:
        assert out.strip().splitlines() == ['sonnet,opus,haiku', 'pinned-model', 'catalogue-model'], out


def t_finished_states_match_the_runner():
    import json
    import re
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
    import ai_jobs
    page = set(json.loads(re.search(r"new Set\((\[[^\]]*\])\)", _const_src(_src(), "FINISHED"))
                          .group(1).replace("'", '"')))
    assert page == ai_jobs.TERMINAL, f"page {sorted(page)} vs runner {sorted(ai_jobs.TERMINAL)}"


def t_repeated_start_is_blocked_and_start_failure_allows_reviewed_retry():
    out = _run_js(_harness() + _func_src(_src(), 'start') + r"""
(async () => {
  state.preview = {payload: {model: 'chosen'}, result: {confirm_preview: 'reviewed', can_start: true}};
  let release;
  replies.start = new Promise(resolve => { release = resolve; });
  const pending = start();
  await start();
  console.log(requests.filter(x => x === 'start').length, $id('ai-start').disabled);
  release({ok: false, error: {message: 'CLI unavailable'}});
  await pending;
  console.log($id('ai-feedback-title').textContent, $id('ai-start').disabled, state.job);
})();
""")
    if out is not None:
        assert out.strip().splitlines() == ['1 true', 'Analysis could not start false null'], out


def t_changed_inputs_discard_pending_preview_and_restore_preparation_control():
    out = _run_js(_harness() + 'const payload = () => ({});\n' + _func_src(_src(), 'preview') + r"""
(async () => {
  state.selection = {sessions: [{}]};
  let release;
  replies.preview = new Promise(resolve => { release = resolve; });
  const pending = preview();
  await preview();
  console.log(requests.filter(x => x === 'preview').length, $id('ai-preview-button').disabled);
  state.generation++;
  release({ok: true});
  await pending;
  console.log(state.previewRequest, $id('ai-preview-button').disabled, state.preview);
})();
""")
    if out is not None:
        assert out.strip().splitlines() == ['1 true', 'null false undefined'], out


def t_cancellation_and_connection_errors_keep_the_current_job_identity():
    out = _run_js(_harness() + _func_src(_src(), 'cancel') + r"""
(async () => {
  state.job = 'Y';
  replies.cancel = {ok: true};
  await cancel();
  replies.status = {ok: true, busy: true, active: {id: 'Y', state: 'running'}};
  await status();
  console.log($id('ai-feedback-title').textContent, $id('ai-cancel').disabled);
  replies.status = {ok: false};
  await status();
  console.log($id('ai-feedback-title').textContent, state.job);
})();
""")
    if out is not None:
        assert out.strip().splitlines() == ['Stopping analysis true', 'Analysis status unavailable Y'], out


def t_slow_old_report_cannot_replace_a_newer_completed_run():
    out = _run_js(_harness() + r"""
(async () => {
  let release;
  replies.job = {X: new Promise(resolve => {release = resolve;}), Y: {...run('Y', 'completed', REPORT), requested_model: 'NEW'}};
  state.job = 'X';
  replies.status = {ok: true, busy: false, active: null};
  const older = status();
  await new Promise(resolve => setImmediate(resolve));
  replies.status = {ok: true, busy: true, active: {id: 'Y', state: 'running'}};
  await status();
  replies.status = {ok: true, busy: false, active: null};
  await status();
  release({...run('X', 'completed', REPORT), requested_model: 'OLD'});
  await older;
  console.log($id('ai-report').children.some(c => c.textContent.includes('NEW')),
              $id('ai-report').children.some(c => c.textContent.includes('OLD')), state.job);
})();
""")
    if out is not None:
        assert out.strip() == 'true false null', out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

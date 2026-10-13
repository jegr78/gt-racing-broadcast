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
    i = src.index(f"\nasync function {name}(") + 1
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
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; this.disabled = false; }
  set textContent(v) { this._text = v; this.children = []; }
  get textContent() { return this._text; }
  append(c) { this.children.push(c); }
  count(tag) { return this.children.reduce((n, c) => n + (c.tag === tag) + c.count(tag), 0); }
  scrollIntoView() {}
}
const els = {}; const $id = id => els[id] || (els[id] = new El('div'));
const document = {createElement: tag => new El(tag)};
const node = (tag, text, parent) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (parent) parent.append(e); return e; };
const say = (id, text) => { $id(id).textContent = text || ''; };
const error = doc => doc?.error?.message || 'Analysis operation failed';
const state = {generation: 0, job: null, lastJob: null};
const replies = {}, opened = [];
const request = async (op, payload) => { if (op === 'job') opened.push(payload.query.id);
  return op === 'job' ? replies.job[payload.query.id] : replies[op]; };
const history = async () => {}, invalidate = () => {}, tmOpenReference = async () => true;
const run = (id, st, report) => ({ok: true, id, state: st, profile: 'a', requested_model: 'm',
  progress: st, error: st === 'failed' ? {message: 'Analysis execution failed'} : null, report});
const REPORT = {language: 'en', limitations: [], exercises: [],
  findings: [{priority: 1, title: 'Brake later', evidence: [], telemetry: {}, location_m: 500}]};
"""


def _harness():
    s = _src()
    consts = "".join(_const_src(s, n) + "\n" for n in ("FINISHED",) if f"\nconst {n}=" in s)
    return _HARNESS + consts + _func_src(s, "openRun") + "\n" + _func_src(s, "status") + "\n"


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
  console.log([before, $id('ai-report').count('article'), $id('ai-run-status').textContent.split(' ')[0],
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
  await status(); const running = $id('ai-run-status').textContent;
  replies.status = {ok: true, busy: true, active: {id: 'Z', state: 'failed', foreign: true}};
  await status(); const foreign = $id('ai-run-status').textContent;
  console.log([running, foreign.startsWith('An analysis is running in another profile'), opened.length].join('|'));
})();""")
    if out is not None:
        assert out.strip() == "running · Provider running|true|0", out


def t_finished_states_match_the_runner():
    import json
    import re
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
    import ai_jobs
    page = set(json.loads(re.search(r"new Set\((\[[^\]]*\])\)", _const_src(_src(), "FINISHED"))
                          .group(1).replace("'", '"')))
    assert page == ai_jobs.TERMINAL, f"page {sorted(page)} vs runner {sorted(ai_jobs.TERMINAL)}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

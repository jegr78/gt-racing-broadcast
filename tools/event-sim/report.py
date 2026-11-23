"""report.json and report.html of an event-simulation run."""
import html
import json
import os

STYLE = """
body{font:14px/1.4 system-ui,sans-serif;margin:16px;background:#fff;color:#1a1a1a}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#f2f2f2}.ok{color:#11702a}.failed,.hard{color:#b00020;font-weight:600}.skipped,.soft{color:#8a5a00}
img{max-width:240px;display:block}code{font-size:12px}ul{margin:0;padding-left:18px}
.acts{font-size:12px;color:#555}.snap{border:1px solid #ccc;padding:8px;margin:8px 0}
.snap pre{white-space:pre-wrap;font-size:12px;margin:4px 0}
"""
SLOWEST = 10             # actions in the report's slowest list
SNAP_LINES = 12          # console lines and failed requests a snapshot shows in HTML


LEGEND = (
    "<h2>How findings are rated</h2><ul>"
    "<li class='hard'>Hard (the run fails): an action error such as an HTTP error or a missing "
    "control, a check still false at its timeout, a trigger that never fires, a script error "
    "on a page, a traceback in a service log.</li>"
    "<li class='soft'>Soft (reported only): a soft check, a check slower than the tolerance, "
    "a page action or screenshot over its time budget, "
    "a failed resource load on a page (an idle feed's preview answers 503), an ERROR line "
    "in a service log (each distinct line once, with its count), a missing screenshot.</li>"
    "</ul>")


def _e(value):
    return html.escape("" if value is None else str(value))


def _checks(checks):
    if not checks:
        return ""
    items = []
    for c in checks:
        mark = "ok" if c["ok"] else ("soft" if c["soft"] else "hard")
        extra = f" ({_e(c['detail'])})" if c.get("detail") else ""
        items.append(f"<li class='{mark}'><code>{_e(c['expr'])}</code> {c['elapsed']:.2f}s{extra}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _step_findings(items):
    if not items:
        return ""
    return "<ul>" + "".join(f"<li class='{_e(f['severity'])}'>{_e(f['message'])}</li>" for f in items) + "</ul>"


def _actions(actions):
    if not actions:
        return ""
    items = "".join(f"<li class='{'soft' if a.get('slow') else ('ok' if a.get('ok') else 'hard')}'>"
                    f"{'&nbsp;&nbsp;' * a.get('depth', 0)}{_e(a['kind'])} {_e(a['what'])} "
                    f"{(a.get('s') or 0):.2f}s</li>" for a in actions)
    return f"<ul class='acts'>{items}</ul>"


def _long(tasks):
    if not tasks:
        return ""
    return "".join(f"<div class='acts'>{_e(role)} long tasks: "
                   + (", ".join(f"{t['ms']} ms {t['ago_s']:.1f}s before the end" if "ms" in t else _e(t.get("error"))
                                for t in items) or "none") + "</div>" for role, items in tasks.items())


def _host(h):
    if not h or h.get("cpu_s") is None:
        return ""
    share = "" if h.get("cpu_stall_share") is None else f" ({h['cpu_stall_share']:.0%})"
    return (f"<div class='acts'>unit CPU {h['cpu_s']:.2f}s, waiting for CPU {_e(h.get('unit_cpu_stall_s'))}s"
            f"{share}, throttled {_e(h.get('throttled_s'))}s</div>")


def _snapshot(step, snap):
    page = snap.get("page") or {}
    host = snap.get("host") or {}
    unit = host.get("unit") or {}
    facts = {"page": {k: page.get(k) for k in ("url", "alive", "answered_s", "visibility", "focus", "ready",
                                               "title", "error", "metrics", "processes") if k in page},
             "loadavg": host.get("loadavg"), "host pressure": host.get("pressure"),
             "unit": {k: unit.get(k) for k in ("cpu", "memory_current", "memory_peak", "memory_events",
                                               "pids_current", "pressure")} if unit else None,
             "since the step began": snap.get("since_step"),
             "unit processes (CPU s so far)": snap.get("processes")}
    lines = [f"{c.get('age_s')}s ago [{c.get('type')}] {c.get('text')}" for c in page.get("console", [])[-SNAP_LINES:]]
    lines += [f"{n.get('age_s')}s ago {n.get('method', '')} {n.get('url')} {n.get('failure') or n.get('status')}"
              for n in page.get("network", [])[-SNAP_LINES:]]
    picture = (f"<a href='{_e(snap['picture'])}'><img src='{_e(snap['picture'])}' alt='{_e(snap['picture'])}'></a>"
               f"<small>picture took {_e(snap.get('picture_s'))}s</small>") if snap.get("picture") else \
        f"<p>No picture: {_e(snap.get('picture_error'))}</p>"
    return (f"<div class='snap'><b>{_e(step['id'])}</b> {_e(snap['role'])} at {snap['at']:.1f}s: {_e(snap['why'])}"
            f"<pre>{_e(json.dumps(facts, indent=1, default=str))}</pre>"
            + (f"<pre>{_e(chr(10).join(lines))}</pre>" if lines else "") + f"{picture}</div>")


def render_html(data):
    hard = [f for f in data["findings"] if f["severity"] == "hard"]
    soft = sorted((f for f in data["findings"] if f["severity"] == "soft"), key=lambda f: f.get("role") or "")
    verdict = "PASS" if data["ok"] else "FAIL"
    by_step = {}
    for f in data["findings"]:
        if f.get("step"):
            by_step.setdefault(f["step"], []).append(f)
    rows = []
    for s in data["steps"]:
        shot = "".join(f"<a href='{_e(p)}'><img src='{_e(p)}' alt='{_e(p)}'></a>"
                       for p in (s.get("shot"), s.get("fail_shot")) if p)
        shot += "".join(f"<a href='{_e(t)}'>{_e(t)}</a><br>" for t in s.get("traces") or [])
        started = "" if s.get("started") is None else f"{s['started']:.1f}s"
        rows.append(f"<tr><td>{started}</td><td>{_e(s['role'])}</td><td>{_e(s['do'])}"
                    f"<br><small>{_e(s['id'])}</small></td><td class='{_e(s['status'])}'>"
                    f"{_e(s['status'])}<br>{_e(s['error'])}</td><td>{s['duration']:.2f}s"
                    f"{_actions(s.get('actions'))}{_host(s.get('host'))}{_long(s.get('long_tasks'))}</td>"
                    f"<td>{_checks(s['checks'])}</td><td>{_step_findings(by_step.get(s['id']))}</td>"
                    f"<td>{shot}</td></tr>")

    def memory(name, m):
        heap, nodes, listeners = ("?" if m.get(k) is None else m[k] for k in ("heap_mb", "dom_nodes", "listeners"))
        return (f"<li>{_e(name)}: {_e(heap)} MB JS heap, {_e(nodes)} DOM nodes, "
                f"{_e(listeners)} event listeners</li>")
    snaps = "".join(_snapshot(s, snap) for s in data["steps"] for snap in s.get("snapshots") or [])
    slowest = sorted(((a, s["id"]) for s in data["steps"] for a in s.get("actions") or [] if a.get("s") is not None),
                     key=lambda pair: -pair[0]["s"])[:SLOWEST]
    slow_list = "<ul>" + "".join(f"<li>{a['s']:.2f}s {_e(step)}: {_e(a['role'])} {_e(a['kind'])} {_e(a['what'])}</li>"
                                 for a, step in slowest) + "</ul>" if slowest else "<p>None.</p>"
    procs = ("<ul>" + "".join(f"<li>{p['cpu_s']:.1f}s {_e(p['name'])}</li>" for p in data["unit_processes"][:15])
             + "</ul>") if data.get("unit_processes") else "<p>Not measured (Linux only).</p>"
    pages = data.get("pages") or {}
    memory_list = ("<ul>" + "".join(memory(n, m) for n, m in sorted(pages.items())) + "</ul>"
                   if pages else "<p>No page was open.</p>")

    def finding_list(items):
        if not items:
            return "<p>None.</p>"
        return "<ul>" + "".join(
            f"<li class='{_e(f['severity'])}'>{_e(f.get('role') or 'run')}"
            f"{' / ' + _e(f['step']) if f.get('step') else ''}: {_e(f['message'])}</li>"
            for f in items) + "</ul>"

    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Event simulation {_e(data['scenario'])}</title><style>{STYLE}</style></head><body>"
            f"<h1>{_e(data['scenario'])}: <span class='{'ok' if data['ok'] else 'failed'}'>{verdict}</span></h1>"
            f"<p>{_e(data.get('description'))} Duration {data['duration']:.1f}s.</p>"
            f"<h2>Hard failures</h2>{finding_list(hard)}"
            f"<h2>Soft findings</h2>{finding_list(soft)}"
            f"<h2>Failure snapshots</h2>{snaps or '<p>None.</p>'}"
            f"<h2>Slowest page actions</h2>{slow_list}"
            f"<h2>Page memory at the end</h2>{memory_list}"
            f"<h2>CPU of the run's processes at the end</h2>{procs}"
            f"<h2>Timeline</h2><table><tr><th>Time</th><th>Role</th><th>Action</th><th>Result</th>"
            f"<th>Duration</th><th>Checks</th><th>Findings</th><th>Shot</th></tr>{''.join(rows)}</table>"
            f"{LEGEND}</body></html>\n")


def write(out_dir, data):
    """Write report.json and report.html into `out_dir`; returns the HTML path."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
        fh.write("\n")
    path = os.path.join(out_dir, "report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_html(data))
    return path

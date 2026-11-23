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
"""


LEGEND = (
    "<h2>How findings are rated</h2><ul>"
    "<li class='hard'>Hard (the run fails): an action error such as an HTTP error or a missing "
    "control, a check still false at its timeout, a trigger that never fires, a script error "
    "on a page, a traceback in a service log.</li>"
    "<li class='soft'>Soft (reported only): a soft check, a check slower than the tolerance, "
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


def render_html(data):
    hard = [f for f in data["findings"] if f["severity"] == "hard"]
    soft = sorted((f for f in data["findings"] if f["severity"] == "soft"), key=lambda f: f.get("role") or "")
    verdict = "PASS" if data["ok"] else "FAIL"
    rows = []
    for s in data["steps"]:
        shot = "".join(f"<a href='{_e(p)}'><img src='{_e(p)}' alt='{_e(p)}'></a>"
                       for p in (s.get("shot"), s.get("fail_shot")) if p)
        started = "" if s.get("started") is None else f"{s['started']:.1f}s"
        rows.append(f"<tr><td>{started}</td><td>{_e(s['role'])}</td><td>{_e(s['do'])}"
                    f"<br><small>{_e(s['id'])}</small></td><td class='{_e(s['status'])}'>"
                    f"{_e(s['status'])}<br>{_e(s['error'])}</td><td>{s['duration']:.2f}s</td>"
                    f"<td>{_checks(s['checks'])}</td><td>{shot}</td></tr>")

    def memory(name, m):
        heap, nodes, listeners = ("?" if m.get(k) is None else m[k] for k in ("heap_mb", "dom_nodes", "listeners"))
        return (f"<li>{_e(name)}: {_e(heap)} MB JS heap, {_e(nodes)} DOM nodes, "
                f"{_e(listeners)} event listeners</li>")
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
            f"<h2>Page memory at the end</h2>{memory_list}"
            f"<h2>Timeline</h2><table><tr><th>Time</th><th>Role</th><th>Action</th><th>Result</th>"
            f"<th>Duration</th><th>Checks</th><th>Shot</th></tr>{''.join(rows)}</table>"
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

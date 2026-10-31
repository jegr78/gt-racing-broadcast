#!/usr/bin/env python3
"""Unit checks for the Event Notes Sheet-tab parser (pure, stdlib only)
and the /event-notes/data relay endpoint."""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_path = os.path.join(HERE, "..", "src", "scripts", "event_notes.py")
_spec = importlib.util.spec_from_file_location("event_notes", _path)
event_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(event_notes)
parse = event_notes.parse_event_notes


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


m = _load("irofeeds_en", ("src", "relay", "racecast-feeds.py"))


def t_basic_rows():
    csv_text = "Heading,Note,Priority\nWelcome,Mind the sponsor read,Important\n,Running order on screen,\n"
    out = parse(csv_text)
    assert out == [
        {"heading": "Welcome", "note": "Mind the sponsor read", "priority": "important"},
        {"heading": "", "note": "Running order on screen", "priority": "info"},
    ], out


def t_header_order_independent():
    csv_text = "Note,Priority,Heading\nDo the thing,info,Hi\n"
    out = parse(csv_text)
    assert out == [{"heading": "Hi", "note": "Do the thing", "priority": "info"}], out


def t_priority_normalisation():
    csv_text = "Heading,Note,Priority\nA,n1,IMPORTANT\nB,n2,important\nC,n3,Info\nD,n4,\nE,n5,banana\n"
    pris = [r["priority"] for r in parse(csv_text)]
    assert pris == ["important", "important", "info", "info", "info"], pris


def t_empty_note_rows_skipped():
    csv_text = "Heading,Note,Priority\nH,,Important\nH2,real note,\n"
    out = parse(csv_text)
    assert [r["note"] for r in out] == ["real note"], out


def t_missing_columns_and_empty_degrade():
    assert parse("") == []
    assert parse("Heading,Priority\nH,Important\n") == []        # no Note column
    assert parse("Heading,Note,Priority\n") == []                 # header only
    # Missing Heading/Priority columns still parse Note rows:
    out = parse("Note\njust a note\n")
    assert out == [{"heading": "", "note": "just a note", "priority": "info"}], out


def _event_notes_client(event_notes_source, event_documents_source=None):
    """make_handler over a real ThreadingHTTPServer. Returns (srv, get)."""
    import json as _json
    import threading as _t
    import urllib.error
    from urllib.request import urlopen

    class _StubFeed:
        def __init__(self, idx):
            self.idx = idx

    class _StubRelay:
        def __init__(self):
            self.feeds = {"A": _StubFeed(0), "B": _StubFeed(1)}

    handler = m.make_handler(_StubRelay(), event_notes_source=event_notes_source,
                             event_documents_source=event_documents_source)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path):
        try:
            with urlopen(base + path, timeout=5) as r:
                return r.status, _json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read())

    return srv, get


def t_endpoint_returns_notes_when_present():
    s = m.EventNotesSource(None)
    s.rows = [{"heading": "Welcome", "note": "Sponsor read", "priority": "important"},
              {"heading": "", "note": "Running order", "priority": "info"}]
    srv, get = _event_notes_client(s)
    try:
        code, body = get("/event-notes/data")
        assert code == 200, code
        assert body["available"] is True, body
        assert body["notes"] == s.rows, body
    finally:
        srv.shutdown()


def t_endpoint_available_false_when_disabled():
    srv, get = _event_notes_client(None)
    try:
        code, body = get("/event-notes/data")
        assert code == 200, code
        assert body["available"] is False, body
        assert body["notes"] == [], body
    finally:
        srv.shutdown()


def _event_info_poll_delays(responses):
    """Run the Event Info poll under node; return the delay (ms) each poll schedules next.
    `responses` is a list of 'fail' | 'ok' | 401, one per poll in order. None without node."""
    import json, re, shutil, subprocess
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    with open(os.path.join(ROOT, "src", "console", "event-info.html"), encoding="utf-8") as fh:
        script = re.search(r"<script>(.*?)</script>", fh.read(), re.S).group(1)
    harness = r"""
const mk = () => ({hidden: false, tabIndex: 0, tagName: 'DIV', children: [], setAttribute() {}, addEventListener() {},
  replaceChildren() {}, append() {}, focus() {}, showModal() {}, close() {}});
const els = {}; const document = {getElementById: id => els[id] || (els[id] = mk()), createElement: () => mk()};
const listeners = {}; const window = {addEventListener: (k, f) => { listeners[k] = f; }};
const answers = %s; const delays = []; let pending = null;
global.setTimeout = (fn, ms) => { if (ms === 8000) return 0; delays.push(ms); pending = fn; return delays.length; };
global.clearTimeout = () => {};
global.AbortController = class { constructor() { this.signal = {}; } abort() {} };
global.fetch = async () => { const a = answers.shift();
  if (a === 'fail') throw new Error('network');
  if (a === 401) return {ok: false, status: 401};
  return {ok: true, status: 200, json: async () => ({notes: [{note: 'Hello'}], documents: []})}; };
const tick = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
(async () => {
%s
  const total = answers.length; await tick();
  while (answers.length) { const f = pending; pending = null; f(); await tick(); }
  console.log(JSON.stringify({delays, shown: !els.notesBtn.hidden}));
})();
""" % (json.dumps(responses), script)
    out = subprocess.run([node, "-"], input=harness, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", check=True, timeout=30)
    return json.loads(out.stdout)


def t_event_info_retries_a_failed_poll_soon():
    # A failed first poll (relay hiccup, slow first request) must not hide the Notes
    # button for a full 30 s refresh period (#1031).
    out = _event_info_poll_delays(["fail", "ok", "ok"])
    if out is None:
        return
    assert out["delays"][:3] == [5000, 30000, 30000], out
    assert out["shown"] is True, out


def t_event_info_keeps_the_slow_period_for_a_definite_answer():
    out = _event_info_poll_delays([401, "ok"])
    if out is None:
        return
    assert out["delays"][:2] == [30000, 30000], out


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")


if __name__ == "__main__":
    run()

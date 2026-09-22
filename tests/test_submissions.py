#!/usr/bin/env python3
"""Stdlib unit checks for the commentator stream-link submission flow (#193).
Run: python3 tests/test_submissions.py

Covers the pure pending store (src/scripts/cockpit_submissions.py), the relay's
own-row resolver + Discord payload builder, and the live HTTP surface
(POST /cockpit/submit, GET /submissions, POST /submissions/{approve,reject})."""
import importlib.util
import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ca = _load("console_auth", ("src", "scripts", "console_auth.py"))
cs = _load("cockpit_submissions", ("src", "scripts", "cockpit_submissions.py"))
m = _load("irofeeds", ("src", "relay", "racecast-feeds.py"))

SECRET = "sek"


def _rows():
    # ScheduleSource 4-tuples: (url, streamer, stint, line)
    return [("u0", "Alpha Racing", "S1", 2),
            ("", "Beta", "S2", 3),
            ("", "Alpha Racing", "S3", 4),
            ("u3", "Gamma", "S4", 5)]


def t_store_add_assigns_monotonic_ids():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cockpit-pending.json")
        e1 = cs.add_pending(p, streamer_key="alpha-racing", streamer_name="Alpha Racing",
                            target_line=4, target_stint="S3", proposed_url="u-new",
                            prev_url="", now=100.0)
        e2 = cs.add_pending(p, streamer_key="beta", streamer_name="Beta",
                            target_line=3, target_stint="S2", proposed_url="u-b",
                            prev_url="", now=101.0)
        assert e1["id"] == 1 and e2["id"] == 2
        assert e1["ts"] == 100.0 and e1["proposed_url"] == "u-new"
        ids = [e["id"] for e in cs.list_pending(p)]
        assert ids == [1, 2]


def t_store_ids_monotonic_across_pop():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cockpit-pending.json")
        cs.add_pending(p, streamer_key="a", streamer_name="A", target_line=2,
                       target_stint="S1", proposed_url="u", prev_url="", now=1.0)
        assert cs.pop_pending(p, 1)["id"] == 1
        # the next id must NOT reuse 1 even though the store is now empty
        e = cs.add_pending(p, streamer_key="a", streamer_name="A", target_line=2,
                           target_stint="S1", proposed_url="u2", prev_url="", now=2.0)
        assert e["id"] == 2


def t_store_pop_returns_and_removes():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cockpit-pending.json")
        cs.add_pending(p, streamer_key="a", streamer_name="A", target_line=2,
                       target_stint="S1", proposed_url="u", prev_url="", now=1.0)
        cs.add_pending(p, streamer_key="b", streamer_name="B", target_line=3,
                       target_stint="S2", proposed_url="u2", prev_url="", now=2.0)
        got = cs.pop_pending(p, 1)
        assert got["streamer_key"] == "a"
        assert [e["id"] for e in cs.list_pending(p)] == [2]
        assert cs.pop_pending(p, 999) is None       # unknown id -> None, no change
        assert [e["id"] for e in cs.list_pending(p)] == [2]


def t_store_caps_oldest_dropped():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cockpit-pending.json")
        for i in range(5):
            cs.add_pending(p, streamer_key="a", streamer_name="A", target_line=2,
                           target_stint="S1", proposed_url=f"u{i}", prev_url="",
                           now=float(i), max_pending=3)
        kept = cs.list_pending(p)
        assert len(kept) == 3
        assert [e["id"] for e in kept] == [3, 4, 5]   # oldest (1,2) dropped


def t_store_load_missing_and_corrupt():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cockpit-pending.json")
        assert cs.list_pending(p) == []                # missing -> []
        with open(p, "w") as fh:
            fh.write("{not json")
        assert cs.list_pending(p) == []                # corrupt -> [] (never throws)


def t_store_validate_rejects_bad_shapes():
    for bad in ({"pending": "x"}, {"pending": [{"id": "x"}]},
                {"pending": [{"id": 1, "streamer_key": "BAD KEY"}]}, []):
        try:
            cs.validate_pending(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def t_add_pending_records_mode():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "pending.json")
    e = cs.add_pending(path, streamer_key="alice", streamer_name="Alice",
                       target_line=5, target_stint="Q", proposed_url="https://youtu.be/x",
                       prev_url="", now=1.0, mode="qualifying")
    assert e["mode"] == "qualifying"
    # default when omitted
    e2 = cs.add_pending(path, streamer_key="bob", streamer_name="Bob",
                        target_line=6, target_stint="2", proposed_url="https://youtu.be/y",
                        prev_url="", now=2.0)
    assert e2["mode"] == "race"


def t_validate_entry_defaults_missing_mode_to_race():
    legacy = {"id": 1, "streamer_key": "alice", "streamer_name": "Alice",
              "target_line": 5, "target_stint": "Q", "proposed_url": "https://youtu.be/x",
              "prev_url": "", "ts": 1.0}          # no mode field (pre-upgrade entry)
    seq, entries = cs.validate_pending({"seq": 1, "pending": [legacy]})
    assert entries[0]["mode"] == "race"


def t_audit_appends_jsonl():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cockpit-submissions.log")
        cs.append_audit(p, {"event": "submit", "id": 1, "ts": 1.0})
        cs.append_audit(p, {"event": "approve", "id": 1, "ts": 2.0})
        with open(p, encoding="utf-8") as fh:
            lines = [json.loads(x) for x in fh.read().splitlines()]
        assert [r["event"] for r in lines] == ["submit", "approve"]


def t_resolve_by_stint_own_row():
    ok, target = m.own_submission_target(_rows(), "alpha-racing", stint="S3")
    assert ok is True
    assert target["target_line"] == 4 and target["target_stint"] == "S3"
    assert target["streamer_name"] == "Alpha Racing" and target["prev_url"] == ""


def t_resolve_rejects_foreign_stint():
    # S2 belongs to Beta, not Alpha -> rejected for alpha-racing
    ok, err = m.own_submission_target(_rows(), "alpha-racing", stint="S2")
    assert ok is False and "assigned to you" in err.lower()


def t_resolve_rejects_when_not_scheduled():
    ok, err = m.own_submission_target(_rows(), "nobody", stint="S1")
    assert ok is False


def t_resolve_by_row_index_checks_ownership():
    # schedule row 1 (1-based) is Alpha's first stint
    ok, target = m.own_submission_target(_rows(), "alpha-racing", row=1)
    assert ok is True and target["target_line"] == 2
    # row 2 is Beta's -> alpha cannot target it
    ok, err = m.own_submission_target(_rows(), "alpha-racing", row=2)
    assert ok is False


def t_own_stints_lists_only_mine_with_link_and_url():
    st = m.cockpit_own_stints(_rows(), "alpha-racing")
    assert [s["stint"] for s in st] == ["S1", "S3"]
    assert st[0]["row"] == 1 and st[0]["has_link"] is True and st[0]["url"] == "u0"
    assert st[1]["row"] == 3 and st[1]["has_link"] is False and st[1]["url"] == ""
    # Carries ONLY the commentator's own URLs, so the cockpit can pre-fill own
    # links without leaking a foreign row's (u3, Gamma).
    assert all(s["url"] != "u3" for s in st)


def t_submission_payload_shape():
    e = {"streamer_name": "Alpha Racing", "target_stint": "S3", "proposed_url": "u-new"}
    payload = m.cockpit_submission_payload(e, pending_count=2)
    assert payload["content"] == "@here"
    assert payload["allowed_mentions"]["parse"] == ["everyone"]
    body = json.dumps(payload)
    assert "Alpha Racing" in body and "S3" in body


def t_approval_payload_has_no_ping():
    # The director-approval note is informational: no @here mention and no
    # top-level `content`, but it still names the commentator, stint and link.
    e = {"streamer_name": "Alpha Racing", "target_stint": "S3", "proposed_url": "u-new"}
    payload = m.cockpit_approval_payload(e)
    assert "@here" not in json.dumps(payload)
    assert not payload.get("content")
    assert payload["allowed_mentions"]["parse"] == []
    body = json.dumps(payload)
    assert "Alpha Racing" in body and "S3" in body and "u-new" in body


def t_payloads_carry_event_title():
    # A non-empty event title appears in both the submission and approval embeds,
    # and the submission footer keeps its pending count alongside it. An empty title
    # leaves a count-only submission footer and no approval footer. (#207)
    e = {"streamer_name": "Alpha Racing", "target_stint": "S3", "proposed_url": "u-new"}
    sub = m.cockpit_submission_payload(e, pending_count=2, event_title="GTEC - Round 4")
    assert sub["embeds"][0]["footer"]["text"] == "GTEC - Round 4 · 2 pending"
    sub0 = m.cockpit_submission_payload(e, pending_count=2)
    assert sub0["embeds"][0]["footer"]["text"] == "2 pending"
    app = m.cockpit_approval_payload(e, event_title="GTEC - Round 4")
    assert app["embeds"][0]["footer"] == {"text": "GTEC - Round 4"}
    assert "footer" not in m.cockpit_approval_payload(e)["embeds"][0]


def _client(secret=SECRET, rows=None, live_idx=0,
            submission_path=None, audit_path=None, setup_ctl="default",
            webhook=None):
    """make_handler over a real server, wired with a submission store + a
    recording setup_ctl stub. Returns (server, get, post, calls)."""
    import threading as _t
    import urllib.error
    from urllib.request import Request, urlopen

    calls = []

    class _Feed:
        def __init__(self, idx):
            self.idx = idx

    class _Source:
        def __init__(self, rws):
            self._rows = rws

        def get_rows(self):
            return list(self._rows)

        def health(self):
            return {"count": len(self._rows)}

    class _Relay:
        def __init__(self):
            self.source = _Source(rows if rows is not None else _rows())
            self.mode = "race"
            self.feeds = {"A": _Feed(live_idx), "B": _Feed(live_idx + 1)}
            self.discord_webhook_url = webhook
            self._desync = {"active": False}   # mirrors Relay.__init__ (#494)

        def live_feed(self):
            return "A"

        def on_air_row_idx(self):
            return self.feeds[self.live_feed()].idx

        def live_row_map(self):
            return {f.idx: k for k, f in self.feeds.items()}

    class _Setup:
        def schedule_set(self, row, url=None, name=None, stint=None):
            calls.append({"tab": "race", "row": row, "url": url, "name": name,
                         "stint": stint})
            return {"ok": True, "row": row}

        def qualifying_set(self, row, url=None, name=None, stint=None):
            calls.append({"tab": "qualifying", "row": row, "url": url, "name": name,
                         "stint": stint})
            return {"ok": True, "row": row}

    store = m.SubmissionStore(submission_path, audit_path) if submission_path else None
    sc = _Setup() if setup_ctl == "default" else setup_ctl
    handler = m.make_handler(_Relay(), setup_ctl=sc, console_secret=secret,
                             submission_store=store)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def _read(req):
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def get(path, cookie=None, headers=None):
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        return _read(Request(base + path, headers=h))

    def post(path, body, cookie=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if cookie:
            h["Cookie"] = cookie
        return _read(Request(base + path, data=json.dumps(body).encode(),
                             headers=h, method="POST"))

    return srv, get, post, calls


def t_cockpit_data_exposes_my_stints():
    with tempfile.TemporaryDirectory() as d:
        srv, get, _post, _c = _client(submission_path=os.path.join(d, "p.json"))
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            code, _h, body = get("/cockpit/data?t=" + tok)
            assert code == 200, code
            data = json.loads(body)
            assert data["submit_enabled"] is True
            assert [s["stint"] for s in data["my_stints"]] == ["S1", "S3"]
            # An own-stint URL is surfaced so the cockpit can pre-fill it; a
            # foreign commentator's URL (Gamma's u3) is NEVER exposed.
            assert any(s.get("url") == "u0" for s in data["my_stints"])
            assert "u3" not in body.decode()
        finally:
            srv.shutdown()


def t_cockpit_data_exposes_my_pending():
    # After submitting, /cockpit/data surfaces the commentator's OWN pending entries
    # (stint and id, never a URL) so the cockpit shows a status that clears once the
    # director acts. A different commentator sees none.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        srv, get, post, _c = _client(submission_path=p)
        try:
            tok_a = ca.mint_token(SECRET, "alpha-racing")
            tok_b = ca.mint_token(SECRET, "beta")
            post("/cockpit/submit",
                 {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"},
                 cookie="rc_console=" + tok_a)
            da = json.loads(get("/cockpit/data?t=" + tok_a)[2])
            db = json.loads(get("/cockpit/data?t=" + tok_b)[2])
            assert [x["stint"] for x in da["my_pending"]] == ["S3"]
            assert "id" in da["my_pending"][0]
            assert db["my_pending"] == []                  # only the submitter's own
            assert "youtube" not in json.dumps(da["my_pending"]).lower()  # no URL leak
        finally:
            srv.shutdown()


def t_submit_requires_auth():
    with tempfile.TemporaryDirectory() as d:
        srv, _get, post, _c = _client(submission_path=os.path.join(d, "p.json"))
        try:
            code, _h, _b = post("/cockpit/submit",
                                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"})
            assert code == 401, code
        finally:
            srv.shutdown()


def t_submit_disabled_is_404():
    with tempfile.TemporaryDirectory() as d:
        srv, _get, post, _c = _client(secret="",
                                      submission_path=os.path.join(d, "p.json"))
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            code, _h, _b = post("/cockpit/submit",
                                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"},
                                cookie="rc_console=" + tok)
            assert code == 404, code
        finally:
            srv.shutdown()


def t_submit_rejects_non_channel_url():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        srv, _get, post, _c = _client(submission_path=p)
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            code, _h, body = post("/cockpit/submit",
                                  {"url": "http://evil.example/x", "stint": "S3"},
                                  cookie="rc_console=" + tok)
            assert code == 400, (code, body)
            assert cs.list_pending(p) == []          # nothing stored
        finally:
            srv.shutdown()


def t_submit_rejects_foreign_row():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        srv, _get, post, _c = _client(submission_path=p)
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            # S2 is Beta's stint
            code, _h, _b = post("/cockpit/submit",
                                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S2"},
                                cookie="rc_console=" + tok)
            assert code == 400, code
            assert cs.list_pending(p) == []
        finally:
            srv.shutdown()


def t_submit_stores_pending_for_own_row():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        srv, get, post, _c = _client(submission_path=p)
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            code, _h, body = post("/cockpit/submit",
                                  {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"},
                                  cookie="rc_console=" + tok)
            assert code == 200, (code, body)
            pend = cs.list_pending(p)
            assert len(pend) == 1
            e = pend[0]
            assert e["streamer_key"] == "alpha-racing" and e["target_line"] == 4
            assert e["target_stint"] == "S3"
            # director list endpoint surfaces it (tailnet-only, no /cockpit prefix)
            code, _h, body = get("/submissions")
            assert code == 200, code
            assert json.loads(body)["pending"][0]["id"] == e["id"]
        finally:
            srv.shutdown()


def t_approve_writes_schedule_and_clears():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        srv, get, post, calls = _client(submission_path=p)
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            post("/cockpit/submit", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"},
                 cookie="rc_console=" + tok)
            sid = cs.list_pending(p)[0]["id"]
            code, _h, body = post("/submissions/approve", {"id": sid})
            assert code == 200, (code, body)
            assert len(calls) == 1
            assert calls[0]["row"] == 4 and calls[0]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            assert calls[0]["stint"] == "S3" and calls[0]["name"] == "Alpha Racing"
            assert cs.list_pending(p) == []          # cleared after approve
        finally:
            srv.shutdown()


def t_approve_routes_by_entry_mode_qualifying_vs_race():
    # Approve branches on the ENTRY's own recorded mode, not the relay's current
    # one: a qualifying submission writes the Qualifying tab via
    # setup_ctl.qualifying_set, a race submission the Schedule tab via
    # setup_ctl.schedule_set.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        srv, _get, post, calls = _client(submission_path=p)
        try:
            e_qual = cs.add_pending(p, streamer_key="alpha-racing",
                                    streamer_name="Alpha Racing", target_line=4,
                                    target_stint="S3", proposed_url="u-qual",
                                    prev_url="", now=100.0, mode="qualifying")
            e_race = cs.add_pending(p, streamer_key="beta", streamer_name="Beta",
                                    target_line=3, target_stint="S2",
                                    proposed_url="u-race", prev_url="", now=101.0,
                                    mode="race")

            code, _h, body = post("/submissions/approve", {"id": e_qual["id"]})
            assert code == 200, (code, body)
            code, _h, body = post("/submissions/approve", {"id": e_race["id"]})
            assert code == 200, (code, body)

            assert len(calls) == 2
            assert calls[0]["tab"] == "qualifying"
            assert calls[0]["row"] == 4 and calls[0]["url"] == "u-qual"
            assert calls[0]["name"] == "Alpha Racing" and calls[0]["stint"] == "S3"
            assert calls[1]["tab"] == "race"
            assert calls[1]["row"] == 3 and calls[1]["url"] == "u-race"
            assert calls[1]["name"] == "Beta" and calls[1]["stint"] == "S2"
            assert cs.list_pending(p) == []          # both cleared after approve
        finally:
            srv.shutdown()


def _recording_webhook():
    """A tiny local HTTP server that captures POSTed Discord payloads. Returns
    (url, captured_list, shutdown_fn)."""
    import threading as _t
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    captured = []

    class _H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            captured.append(json.loads(self.rfile.read(n) or b"{}"))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", captured, srv.shutdown


def t_approve_posts_discord_without_ping():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        hook_url, captured, hook_stop = _recording_webhook()
        srv, get, post, calls = _client(submission_path=p, webhook=hook_url)
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            post("/cockpit/submit", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"},
                 cookie="rc_console=" + tok)
            captured.clear()                         # drop the submission @here ping
            sid = cs.list_pending(p)[0]["id"]
            code, _h, _b = post("/submissions/approve", {"id": sid})
            assert code == 200, code
            assert len(captured) == 1                # one approval note fired
            note = json.dumps(captured[0])
            assert "@here" not in note               # the follow-up: no ping
            assert "Alpha Racing" in note and "S3" in note
        finally:
            hook_stop()
            srv.shutdown()


def t_reject_does_not_post_discord():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        hook_url, captured, hook_stop = _recording_webhook()
        srv, _get, post, _c = _client(submission_path=p, webhook=hook_url)
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            post("/cockpit/submit", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"},
                 cookie="rc_console=" + tok)
            captured.clear()
            sid = cs.list_pending(p)[0]["id"]
            post("/submissions/reject", {"id": sid})
            assert captured == []                    # reject is silent
        finally:
            hook_stop()
            srv.shutdown()


def t_reject_discards_without_writing():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "p.json")
        srv, _get, post, calls = _client(submission_path=p)
        try:
            tok = ca.mint_token(SECRET, "alpha-racing")
            post("/cockpit/submit", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "stint": "S3"},
                 cookie="rc_console=" + tok)
            sid = cs.list_pending(p)[0]["id"]
            code, _h, _b = post("/submissions/reject", {"id": sid})
            assert code == 200, code
            assert calls == []                       # never wrote the schedule
            assert cs.list_pending(p) == []
        finally:
            srv.shutdown()


def t_submissions_list_is_not_under_cockpit_prefix():
    # /submissions is tailnet-only and never funnelled, so it must not require a
    # cockpit token and must work even when the cockpit is disabled.
    with tempfile.TemporaryDirectory() as d:
        srv, get, _post, _c = _client(secret="",
                                      submission_path=os.path.join(d, "p.json"))
        try:
            code, _h, body = get("/submissions")
            assert code == 200, code
            assert json.loads(body)["pending"] == []
        finally:
            srv.shutdown()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

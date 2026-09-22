#!/usr/bin/env python3
"""Stdlib unit checks for the free-text event title. (#207)
Run: python3 tests/test_event_title.py

Covers the pure sanitizer, the EventTitleStore persistence + precedence
(modeled on TimerStore), and the live HTTP surface (POST /event/title persists +
echoes, GET /status and GET /cockpit/data expose the title)."""
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
m = _load("irofeeds", ("src", "relay", "racecast-feeds.py"))

SECRET = "sek"


# The pure sanitizer.

def t_sanitize_trims_and_passes_normal_text():
    assert m.sanitize_event_title("  GTEC - 2026 - Round 4  ") == "GTEC - 2026 - Round 4"


def t_sanitize_keeps_unicode():
    assert m.sanitize_event_title("Nürburgring 24h — Round 4") == "Nürburgring 24h — Round 4"


def t_sanitize_strips_control_chars():
    # newlines, tabs and the rest collapse out; a title is one line.
    assert m.sanitize_event_title("Round\n4\tNürburgring\r") == "Round4Nürburgring"


def t_sanitize_caps_length():
    out = m.sanitize_event_title("x" * 500)
    assert len(out) == m.EVENT_TITLE_MAX == 120


def t_sanitize_none_and_nonstr_become_empty():
    assert m.sanitize_event_title(None) == ""
    assert m.sanitize_event_title(123) == ""
    assert m.sanitize_event_title("   ") == ""


# EventTitleStore.

def t_store_uses_default_when_no_file():
    with tempfile.TemporaryDirectory() as d:
        st = m.EventTitleStore(os.path.join(d, "event.json"), default="Round 4")
        assert st.get() == "Round 4"


def t_store_default_is_sanitized():
    with tempfile.TemporaryDirectory() as d:
        st = m.EventTitleStore(os.path.join(d, "event.json"), default="  Round\n4  ")
        assert st.get() == "Round4"


def t_store_set_persists_and_reloads():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "event.json")
        st = m.EventTitleStore(path, default="")
        assert st.set("Round 5 — Spa") == "Round 5 — Spa"
        # a fresh store over the same file adopts the persisted title (restart-safe)
        st2 = m.EventTitleStore(path, default="ignored default")
        assert st2.get() == "Round 5 — Spa"


def t_store_file_present_overrides_default():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "event.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"title": "From file"}, fh)
        st = m.EventTitleStore(path, default="From env")
        assert st.get() == "From file"


def t_store_empty_file_title_overrides_default():
    # A producer who cleared the title (file with "") wins over the env default;
    # the live file is the source of truth once it exists.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "event.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"title": ""}, fh)
        st = m.EventTitleStore(path, default="From env")
        assert st.get() == ""


def t_store_corrupt_file_falls_back_to_default():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "event.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        st = m.EventTitleStore(path, default="From env")
        assert st.get() == "From env"


# The live HTTP surface.

def _client(event_default="", event_title=None, rows=None):
    """make_handler over a real server with an EventTitleStore + a minimal relay
    stub. Returns (server, get, post, store, tmpdir-handle-closer)."""
    import threading as _t
    import urllib.error
    from urllib.request import Request, urlopen

    tmp = tempfile.TemporaryDirectory()
    store = m.EventTitleStore(os.path.join(tmp.name, "event.json"),
                              default=event_default)
    if event_title is not None:
        store.set(event_title)

    class _Feed:
        def __init__(self, idx):
            self.idx = idx

    class _Source:
        def get_rows(self):
            return list(rows if rows is not None
                        else [("u0", "Alpha Racing", "S1", 2)])

    class _Relay:
        def __init__(self):
            self.source = _Source()
            self.mode = "race"
            self.feeds = {"A": _Feed(0), "B": _Feed(1)}
            self._desync = {"active": False}   # mirrors Relay.__init__ (#494)

        def live_feed(self):
            return "A"

        def on_air_row_idx(self):
            return self.feeds[self.live_feed()].idx

        def live_row_map(self):
            return {f.idx: k for k, f in self.feeds.items()}

        def status(self):
            return {"schedule_len": 1, "feeds": {}}

    handler = m.make_handler(_Relay(), console_secret=SECRET, event_store=store)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def _read(req):
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(path, cookie=None):
        h = {"Cookie": cookie} if cookie else {}
        return _read(Request(base + path, headers=h))

    def post(path, body):
        return _read(Request(base + path, data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"},
                             method="POST"))

    def close():
        srv.shutdown(); tmp.cleanup()

    return get, post, store, close


def t_status_exposes_event_title():
    get, _post, _store, close = _client(event_default="Round 4")
    try:
        code, body = get("/status")
        assert code == 200, code
        assert json.loads(body)["event_title"] == "Round 4"
    finally:
        close()


def t_status_event_title_empty_when_unset():
    get, _post, _store, close = _client()
    try:
        assert json.loads(get("/status")[1])["event_title"] == ""
    finally:
        close()


def t_post_event_title_sets_persists_and_sanitizes():
    get, post, store, close = _client()
    try:
        code, body = post("/event/title", {"title": "  Round 7 — Le Mans\n  "})
        assert code == 200, code
        res = json.loads(body)
        assert res["ok"] is True and res["title"] == "Round 7 — Le Mans"
        # in-memory + /status reflect it immediately, and it is persisted
        assert store.get() == "Round 7 — Le Mans"
        assert json.loads(get("/status")[1])["event_title"] == "Round 7 — Le Mans"
    finally:
        close()


def t_post_event_title_clears_with_empty():
    get, post, _store, close = _client(event_default="Round 4")
    try:
        post("/event/title", {"title": ""})
        assert json.loads(get("/status")[1])["event_title"] == ""
    finally:
        close()


def t_cockpit_data_exposes_event_title():
    get, _post, _store, close = _client(event_default="Round 4")
    try:
        tok = ca.mint_token(SECRET, "alpha-racing")
        code, body = get("/cockpit/data?t=" + tok)
        assert code == 200, code
        assert json.loads(body)["event_title"] == "Round 4"
    finally:
        close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

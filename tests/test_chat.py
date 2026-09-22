#!/usr/bin/env python3
"""Stdlib unit checks for the crew chat. Run: python3 tests/test_chat.py"""
import importlib.util
import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


ca = _load("chat_admin", ("src", "scripts", "chat_admin.py"))


def t_sanitize_message_basic():
    msg = ca.sanitize_message({"ts": 100.0, "user": "Jens", "text": "hello"})
    assert msg == {"ts": 100.0, "user": "Jens", "text": "hello"}


def t_sanitize_message_trims_and_caps():
    msg = ca.sanitize_message({"ts": 1.0, "user": "x" * 80, "text": "y" * 900})
    assert len(msg["user"]) == ca.MAX_NAME
    assert len(msg["text"]) == ca.MAX_TEXT


def t_sanitize_message_strips_control_chars_keeps_space():
    msg = ca.sanitize_message({"ts": 1.0, "user": "a\x00b", "text": "li\x07ne one"})
    assert msg["user"] == "ab"
    assert msg["text"] == "line one"


def t_sanitize_message_default_name():
    msg = ca.sanitize_message({"ts": 1.0, "user": "   ", "text": "hi"})
    assert msg["user"] == ca.DEFAULT_NAME


def t_sanitize_message_folds_unicode_line_separators():
    # Chat lines are single-row: NEL/LS/PS must collapse to a space, not break.
    msg = ca.sanitize_message({"ts": 1.0, "user": "a",
                               "text": "one two three\x85four"})
    assert msg["text"] == "one two three four"


def t_sanitize_message_rejects_empty_text():
    for bad in ({"ts": 1.0, "user": "a", "text": "   "},
                {"ts": 1.0, "user": "a"},
                {"ts": 1.0, "user": "a", "text": 5}):
        assert ca.sanitize_message(bad) is None, bad


def t_sanitize_message_rejects_bad_ts():
    for bad in ({"user": "a", "text": "hi"},
                {"ts": "soon", "user": "a", "text": "hi"},
                {"ts": True, "user": "a", "text": "hi"}):
        assert ca.sanitize_message(bad) is None, bad


def t_validate_payload_ok():
    payload = {"messages": [{"ts": 2.0, "user": "B", "text": "two"},
                            {"ts": 1.0, "user": "A", "text": "one"}]}
    msgs = ca.validate_payload(payload)
    assert [x["ts"] for x in msgs] == [1.0, 2.0]          # sorted by ts
    assert all(set(x) == {"ts", "user", "text"} for x in msgs)


def t_validate_payload_empty_is_valid():
    assert ca.validate_payload({"messages": []}) == []


def t_validate_payload_drops_bad_entries_keeps_good():
    payload = {"messages": [{"ts": 1.0, "user": "A", "text": "ok"},
                            {"user": "A", "text": "no ts"},
                            "garbage"]}
    msgs = ca.validate_payload(payload)
    assert len(msgs) == 1 and msgs[0]["text"] == "ok"


def t_validate_payload_caps_to_max_messages():
    payload = {"messages": [{"ts": float(i), "user": "A", "text": str(i)}
                            for i in range(ca.MAX_MESSAGES + 50)]}
    msgs = ca.validate_payload(payload)
    assert len(msgs) == ca.MAX_MESSAGES
    assert msgs[-1]["text"] == str(ca.MAX_MESSAGES + 49)   # newest kept


def t_validate_payload_rejects_malformed_shape():
    for bad in (None, [], 5, "x", {"messages": 5}, {"messages": {"a": 1}}, {}):
        try:
            ca.validate_payload(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def t_write_then_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        msgs = [{"ts": 1.0, "user": "A", "text": "one"}]
        ca.write_messages(path, msgs)
        assert ca.load_messages(path) == msgs


def t_load_missing_or_corrupt_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        assert ca.load_messages(os.path.join(d, "nope.json")) == []
        bad = os.path.join(d, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        assert ca.load_messages(bad) == []


def t_load_sanitizes_hand_edited_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"messages": [{"ts": 1.0, "user": "A", "text": "hi"},
                                    {"user": "no ts", "text": "drop"}]}, fh)
        assert ca.load_messages(path) == [{"ts": 1.0, "user": "A", "text": "hi"}]


def t_write_is_atomic_no_partial_temp_left():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        ca.write_messages(path, [{"ts": 1.0, "user": "A", "text": "x"}])
        assert set(os.listdir(d)) == {"chat.json"}   # no leftover *.tmp


m = _load("irofeeds", ("src", "relay", "racecast-feeds.py"))


def _store(tmp):
    return m.ChatStore(os.path.join(tmp, "chat.json"))


def t_chatstore_add_sets_server_ts_ignores_client_ts():
    with tempfile.TemporaryDirectory() as d:
        cs = _store(d)
        r = cs.add(user="Jens", text="hi", now=123.0)
        assert r["ok"] is True
        msg = r["message"]
        assert msg["ts"] == 123.0 and msg["user"] == "Jens" and msg["text"] == "hi"
        assert cs.data()["messages"] == [msg]


def t_chatstore_add_rejects_empty_text():
    with tempfile.TemporaryDirectory() as d:
        cs = _store(d)
        assert "error" in cs.add(user="x", text="   ", now=1.0)
        assert cs.data()["messages"] == []


def t_chatstore_add_caps_and_strips():
    with tempfile.TemporaryDirectory() as d:
        cs = _store(d)
        msg = cs.add(user="n" * 99, text="a\x07b" + "c" * 999, now=1.0)["message"]
        assert len(msg["user"]) == ca.MAX_NAME and len(msg["text"]) == ca.MAX_TEXT
        assert "\x07" not in msg["text"]


def t_chatstore_ring_buffer_cap():
    with tempfile.TemporaryDirectory() as d:
        cs = _store(d)
        for i in range(ca.MAX_MESSAGES + 30):
            cs.add(user="A", text=str(i), now=float(i))
        msgs = cs.data()["messages"]
        assert len(msgs) == ca.MAX_MESSAGES
        assert msgs[-1]["text"] == str(ca.MAX_MESSAGES + 29)
        assert msgs[0]["text"] == str(30)


def t_chatstore_persists_and_reloads_from_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        cs = m.ChatStore(path)
        cs.add(user="A", text="kept", now=5.0)
        assert m.ChatStore(path).data()["messages"][0]["text"] == "kept"  # new store loads file


def t_chatstore_reload_adopts_external_file_write():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        cs = m.ChatStore(path)
        cs.add(user="A", text="old", now=1.0)
        ca.write_messages(path, [{"ts": 9.0, "user": "B", "text": "new"}])  # external overwrite
        r = cs.reload()
        assert r["ok"] is True and r["count"] == 1
        assert cs.data()["messages"][0]["text"] == "new"


def t_chatstore_reload_corrupt_keeps_current_buffer():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        cs = m.ChatStore(path)
        cs.add(user="A", text="live", now=1.0)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        r = cs.reload()
        assert "error" in r
        assert cs.data()["messages"][0]["text"] == "live"   # buffer untouched


# Endpoint routing, against a real server on an ephemeral port.

def _chat_client(chat_store, setup_ctl=None):
    """Stand up make_handler over a real ThreadingHTTPServer on 127.0.0.1 and an
    ephemeral port. Returns (server, get, post); the caller must call
    srv.shutdown() in a finally block.
    """
    import json as _json
    import threading as _t
    import urllib.error
    from urllib.request import Request, urlopen

    class _StubFeed:
        def __init__(self, idx):
            self.idx = idx

    class _StubSource:
        def __init__(self, rows):
            self._rows = rows

        def get_rows(self):
            return list(self._rows)

        def health(self):
            return {"count": len(self._rows), "last_ok_age_s": 0.0, "last_error": None}

    class _StubRelay:
        def __init__(self):
            self.source = _StubSource([
                ("https://www.youtube.com/watch?v=a", "Alpha", "", 2),
                ("UCLA_DiR1FfKNvjuUpBHmylQ", "Beta", "", 3),
            ])
            self.feeds = {"A": _StubFeed(0), "B": _StubFeed(1)}

        def status(self):
            return {"schedule_len": 2, "feeds": {}}

    handler = m.make_handler(_StubRelay(), chat_store=chat_store, setup_ctl=setup_ctl)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def _read(req):
        try:
            with urlopen(req, timeout=5) as r:
                return _json.loads(r.read())
        except urllib.error.HTTPError as e:
            return _json.loads(e.read())

    def get(path):
        return _read(base + path)

    def post(path, body):
        return _read(Request(
            base + path,
            data=_json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        ))

    return srv, get, post


def t_chat_endpoint_send_and_data():
    """POST /chat/send returns {ok,message}; message appears in GET /chat/data."""
    with tempfile.TemporaryDirectory() as d:
        cs = m.ChatStore(os.path.join(d, "chat.json"))
        srv, get, post = _chat_client(cs)
        try:
            r = post("/chat/send", {"user": "Jens", "text": "hello world"})
            assert r.get("ok") is True, r
            msg = r.get("message", {})
            assert msg.get("user") == "Jens"
            assert msg.get("text") == "hello world"
            assert isinstance(msg.get("ts"), float)

            d_resp = get("/chat/data")
            assert "messages" in d_resp, d_resp
            messages = d_resp["messages"]
            assert len(messages) == 1
            assert messages[0]["user"] == "Jens"
            assert messages[0]["text"] == "hello world"
        finally:
            srv.shutdown()


def t_chat_endpoint_data_returns_all_messages():
    """GET /chat/data returns {"messages": [...]} with all stored messages."""
    with tempfile.TemporaryDirectory() as d:
        cs = m.ChatStore(os.path.join(d, "chat.json"))
        srv, get, post = _chat_client(cs)
        try:
            post("/chat/send", {"user": "A", "text": "first"})
            post("/chat/send", {"user": "B", "text": "second"})
            d_resp = get("/chat/data")
            assert "messages" in d_resp, d_resp
            texts = [m_["text"] for m_ in d_resp["messages"]]
            assert texts == ["first", "second"], texts
        finally:
            srv.shutdown()


def t_chat_endpoint_reload_adopts_external_write():
    """GET /chat/reload returns {ok, count} after chat_admin.write_messages to the same file."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        cs = m.ChatStore(path)
        cs.add(user="Old", text="old message", now=1.0)

        srv, get, post = _chat_client(cs)
        try:
            # Externally overwrite the file with a new message.
            ca.write_messages(path, [{"ts": 99.0, "user": "External", "text": "new message"}])

            r = get("/chat/reload")
            assert r.get("ok") is True, r
            assert r.get("count") == 1, r

            # The in-memory store should now reflect the externally-written message.
            d_resp = get("/chat/data")
            messages = d_resp["messages"]
            assert len(messages) == 1
            assert messages[0]["text"] == "new message"
            assert messages[0]["user"] == "External"
        finally:
            srv.shutdown()


def t_chat_endpoint_send_works_without_setup_ctl():
    """POST /chat/send works even when make_handler is built with setup_ctl=None,
    which holds only while the chat POST branch sits above the 'if not setup_ctl'
    guard in do_POST.
    """
    with tempfile.TemporaryDirectory() as d:
        cs = m.ChatStore(os.path.join(d, "chat.json"))
        srv, get, post = _chat_client(cs, setup_ctl=None)
        try:
            r = post("/chat/send", {"user": "Crew", "text": "no setup ctl"})
            assert r.get("ok") is True, r
            assert r["message"]["text"] == "no setup ctl"
        finally:
            srv.shutdown()


def t_chat_endpoint_no_destructive_clear():
    """GET /chat/clear must NOT clear messages (it returns 404, messages survive).
    Also: POST /chat/clear and POST /chat/import return 404.
    """
    with tempfile.TemporaryDirectory() as d:
        cs = m.ChatStore(os.path.join(d, "chat.json"))
        srv, get, post = _chat_client(cs)
        try:
            post("/chat/send", {"user": "A", "text": "should survive"})

            # GET /chat/clear: must 404, not clear.
            r = get("/chat/clear")
            assert "error" in r, r

            d_resp = get("/chat/data")
            assert len(d_resp["messages"]) == 1
            assert d_resp["messages"][0]["text"] == "should survive"

            # POST /chat/clear and POST /chat/import must 404.
            r_clear = post("/chat/clear", {})
            assert "error" in r_clear, r_clear

            r_import = post("/chat/import", {"messages": []})
            assert "error" in r_import, r_import
        finally:
            srv.shutdown()


def t_chat_endpoint_send_is_post_only():
    """GET /chat/send must return 404 (send is POST-only)."""
    with tempfile.TemporaryDirectory() as d:
        cs = m.ChatStore(os.path.join(d, "chat.json"))
        srv, get, _ = _chat_client(cs)
        try:
            r = get("/chat/send")
            assert "error" in r, r
        finally:
            srv.shutdown()


def t_apply_pull_overwrites_on_valid():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        ca.write_messages(path, [{"ts": 1.0, "user": "old", "text": "old"}])
        n = ca.apply_pulled(path, {"messages": [{"ts": 2.0, "user": "new", "text": "new"}]})
        assert n == 1
        assert ca.load_messages(path) == [{"ts": 2.0, "user": "new", "text": "new"}]


def t_apply_pull_empty_overwrites_to_empty():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        ca.write_messages(path, [{"ts": 1.0, "user": "old", "text": "old"}])
        assert ca.apply_pulled(path, {"messages": []}) == 0
        assert ca.load_messages(path) == []


def t_apply_pull_malformed_leaves_file_untouched():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "chat.json")
        ca.write_messages(path, [{"ts": 1.0, "user": "old", "text": "old"}])
        with open(path, "rb") as fh:
            before = fh.read()
        for bad in (None, {"messages": 5}, "x", []):
            try:
                ca.apply_pulled(path, bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        with open(path, "rb") as fh:
            assert fh.read() == before     # byte-for-byte unchanged


def t_apply_pull_creates_file_when_absent():
    """apply_pulled writes the file even when the path did not previously exist."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "new-chat.json")
        assert not os.path.exists(path)
        payload = {"messages": [{"ts": 5.0, "user": "Alice", "text": "first ever"}]}
        n = ca.apply_pulled(path, payload)
        assert n == 1
        assert os.path.exists(path)
        loaded = ca.load_messages(path)
        assert loaded == [{"ts": 5.0, "user": "Alice", "text": "first ever"}]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

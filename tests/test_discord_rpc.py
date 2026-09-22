#!/usr/bin/env python3
"""Stdlib unit checks for src/scripts/discord_rpc.py. Run: python3 tests/test_discord_rpc.py"""
import importlib.util, json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
spec = importlib.util.spec_from_file_location(
    "discord_rpc", os.path.join(ROOT, "src", "scripts", "discord_rpc.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_ipc_candidates_windows_named_pipe():
    cands = m.ipc_candidates("nt", {})
    assert cands[0] == r"\\?\pipe\discord-ipc-0"
    assert r"\\?\pipe\discord-ipc-9" in cands
    assert all("\\" in c for c in cands)          # never os.path.join'd on the runner


def t_ipc_candidates_posix_order_and_subdirs():
    cands = m.ipc_candidates("posix", {"XDG_RUNTIME_DIR": "/run/user/1000"})
    assert cands[0] == "/run/user/1000/discord-ipc-0"
    assert "/run/user/1000/app/com.discordapp.Discord/discord-ipc-0" in cands
    assert "/tmp/discord-ipc-0" in cands           # always a fallback base


def t_frame_round_trip():
    raw = m.encode_frame(1, {"cmd": "PING"})
    op, length = m.frame_header(raw[:8])
    assert op == 1 and length == len(raw) - 8
    assert json.loads(raw[8:]) == {"cmd": "PING"}
    assert struct.unpack("<II", raw[:8]) == (1, length)   # little-endian, as Discord expects


def t_message_builders():
    assert m.msg_handshake(123) == (0, {"v": 1, "client_id": "123"})
    op, p = m.msg_authorize(123)
    assert op == 1 and p["cmd"] == "AUTHORIZE" and p["args"]["scopes"] == ["rpc"]
    assert m.msg_authenticate("tok")[1]["args"]["access_token"] == "tok"
    op, p = m.msg_select_voice(456)
    assert p["cmd"] == "SELECT_VOICE_CHANNEL" and p["args"] == {"channel_id": "456", "force": True}
    assert m.msg_leave()[1]["args"]["channel_id"] is None


def t_parse_channel_link():
    assert m.parse_channel_link(
        "https://discord.com/channels/111/222") == ("111", "222")
    assert m.parse_channel_link("discord://-/channels/111/222/") == ("111", "222")
    assert m.parse_channel_link("https://discord.com/channels/111") is None   # no channel
    assert m.parse_channel_link("not a link") is None
    assert m.parse_channel_link("") is None
    assert m.parse_channel_link(None) is None
    assert m.parse_channel_link(
        "https://discord.com/channels/@me/222") is None                       # non-numeric guild


def t_discord_voice_from_csv():
    csv_text = ('"Stints","Cue Preset","Discord Voice"\r\n'
                '"Stint 1","Formation Lap",""\r\n'
                '"Stint 2","","https://discord.com/channels/1/2"\r\n')
    assert m.discord_voice_from_csv(csv_text) == "https://discord.com/channels/1/2"
    assert m.discord_voice_from_csv('"A","B"\r\n"x","y"\r\n') == ""   # header absent
    assert m.discord_voice_from_csv("") == ""


def t_resolve_voice_target_override_then_fallback():
    sheet = "https://discord.com/channels/11/22"
    env = "https://discord.com/channels/33/44"
    assert m.resolve_voice_target(sheet, env) == ("11", "22")   # sheet wins
    assert m.resolve_voice_target("", env) == ("33", "44")      # fall back to env
    assert m.resolve_voice_target("garbage", env) == ("33", "44")   # bad sheet -> env
    assert m.resolve_voice_target("", "") is None


def t_token_cache_logic():
    fresh = {"access_token": "a", "expires_at": 1000, "refresh_token": "r"}
    assert m.token_valid(fresh, now=900) is True
    assert m.token_valid(fresh, now=999) is False          # inside the 60s skew
    assert m.token_valid({}, now=0) is False
    expired = {"access_token": "a", "expires_at": 100, "refresh_token": "r"}
    assert m.needs_refresh(expired, now=900) is True
    assert m.needs_refresh({"access_token": "a", "expires_at": 100}, now=900) is False  # no refresh token


def t_store_token_absolute_expiry():
    resp = {"access_token": "a", "refresh_token": "r", "expires_in": 604800, "scope": "rpc identify"}
    cache = m.store_token(resp, now=1000)
    assert cache["access_token"] == "a" and cache["refresh_token"] == "r"
    assert cache["expires_at"] == 1000 + 604800


def t_oauth_bodies():
    ex = m.token_exchange_body("cid", "sec", "code")
    assert ex["grant_type"] == "authorization_code" and ex["redirect_uri"] == "http://localhost"
    assert ex["code"] == "code" and ex["client_secret"] == "sec"
    rf = m.token_refresh_body("cid", "sec", "rtok")
    assert rf["grant_type"] == "refresh_token" and rf["refresh_token"] == "rtok"


def _fake_conn(script):
    """A conn whose recv() replays pre-encoded frames; records sent payloads."""
    class C:
        def __init__(self):
            self.sent = []
            self._buf = b"".join(m.encode_frame(op, p) for op, p in script)
        def sendall(self, data):
            # decode what the client sent so the test can assert on it
            op, length = m.frame_header(data[:8])
            self.sent.append((op, json.loads(data[8:8 + length])))
        def recv(self, n):
            chunk, self._buf = self._buf[:n], self._buf[n:]
            return chunk
        def close(self):
            pass
    return C()


def t_client_join_with_cached_token(tmp=None):
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "tok.json")
    m.save_cache(path, {"access_token": "a", "expires_at": 10_000, "refresh_token": "r"})
    # server frames: READY, AUTHENTICATE ok, SELECT_VOICE_CHANNEL ok
    script = [(m.OP_FRAME, {"evt": "READY", "data": {"user": {"username": "box"}}}),
              (m.OP_FRAME, {"cmd": "AUTHENTICATE", "data": {"user": {"username": "box"}}}),
              (m.OP_FRAME, {"cmd": "SELECT_VOICE_CHANNEL", "data": {"name": "General"}})]
    conn = _fake_conn(script)
    posted = []
    cli = m.DiscordVoiceClient(
        "cid", "sec", path,
        connect=lambda ep: conn, endpoints=["fake-ep"],
        http_post_form=lambda url, form: posted.append((url, form)) or {},
        now=lambda: 5000)
    ok, note = cli.join("11", "22")
    assert ok is True and "General" in note
    assert posted == []                                   # cached token -> no token endpoint call
    cmds = [p.get("cmd") for _, p in conn.sent if p.get("cmd")]
    assert cmds == ["AUTHENTICATE", "SELECT_VOICE_CHANNEL"]   # no AUTHORIZE when cached


def t_client_refreshes_expired_token():
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "tok.json")
    m.save_cache(path, {"access_token": "old", "expires_at": 100, "refresh_token": "r"})
    script = [(m.OP_FRAME, {"evt": "READY", "data": {}}),
              (m.OP_FRAME, {"cmd": "AUTHENTICATE", "data": {"user": {}}}),
              (m.OP_FRAME, {"cmd": "SELECT_VOICE_CHANNEL", "data": {"name": "GT"}})]
    def post(url, form):
        assert form["grant_type"] == "refresh_token"       # refresh, not authorize
        return {"access_token": "new", "refresh_token": "r2", "expires_in": 604800}
    cli = m.DiscordVoiceClient("cid", "sec", path,
                               connect=lambda ep: _fake_conn(script), endpoints=["fake-ep"],
                               http_post_form=post, now=lambda: 9000)
    ok, _ = cli.join("11", "22")
    assert ok is True
    assert m.load_cache(path)["access_token"] == "new"     # cache updated


def t_client_join_returns_false_on_closed_socket():
    class Closed:
        def sendall(self, data): pass
        def recv(self, n): return b""          # peer closed immediately
        def close(self): pass
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "tok.json")
    m.save_cache(path, {"access_token": "a", "expires_at": 10_000, "refresh_token": "r"})
    cli = m.DiscordVoiceClient("cid", "sec", path,
                               connect=lambda ep: Closed(), endpoints=["ep"],
                               http_post_form=lambda u, f: {}, now=lambda: 5000)
    ok, note = cli.join("11", "22")
    assert ok is False and isinstance(note, str)


def t_client_autojoin_no_consent_skips_authorize():
    # allow_consent=False (auto-join): with no cached token, join must NOT open the
    # interactive AUTHORIZE popup. It returns (False, note) and posts nothing.
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "tok.json")   # empty cache
    conn = _fake_conn([(m.OP_FRAME, {"evt": "READY", "data": {}})])   # only handshake is read
    posted = []
    cli = m.DiscordVoiceClient("cid", "sec", path,
                               connect=lambda ep: conn, endpoints=["ep"],
                               http_post_form=lambda u, f: posted.append(f) or {},
                               now=lambda: 5000)
    ok, note = cli.join("11", "22", allow_consent=False)
    assert ok is False and "discord join" in note
    assert posted == []                                    # no token-endpoint call
    cmds = [p.get("cmd") for _, p in conn.sent if p.get("cmd")]
    assert "AUTHORIZE" not in cmds                          # never prompted for consent


def t_client_read_frame_times_out_instead_of_hanging():
    # A Discord that accepts the socket but never answers must not hang: the read is
    # bounded by a watchdog for a pipe-like conn, so join returns (False, note).
    import tempfile
    import threading as _t
    path = os.path.join(tempfile.mkdtemp(), "tok.json")

    class Stall:                     # no settimeout() -> exercises the watchdog path
        def __init__(self): self._c = _t.Event()
        def sendall(self, data): pass
        def recv(self, n): self._c.wait(5); return b""     # unblocks only on close()
        def close(self): self._c.set()

    cli = m.DiscordVoiceClient("cid", "sec", path,
                               connect=lambda ep: Stall(), endpoints=["ep"],
                               http_post_form=lambda u, f: {}, now=lambda: 5000,
                               read_timeout=0.2)
    ok, note = cli.join("11", "22")
    assert ok is False and isinstance(note, str)


def t_client_authorize_error_surfaces_message_not_code():
    # If AUTHORIZE returns an ERROR frame (e.g. invalid_scope because the logged-in
    # account is not an App Tester), the numeric RPC error code must NOT be sent to
    # the token endpoint. The join fails with Discord's message and posts nothing.
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "tok.json")   # empty cache -> reaches AUTHORIZE
    script = [(m.OP_FRAME, {"evt": "READY", "data": {}}),
              (m.OP_FRAME, {"evt": "ERROR", "cmd": "AUTHORIZE",
                            "data": {"code": 5000,
                                     "message": "OAuth2 Error: invalid_scope: ..."}})]
    posted = []
    cli = m.DiscordVoiceClient("cid", "sec", path,
                               connect=lambda ep: _fake_conn(script), endpoints=["ep"],
                               http_post_form=lambda u, f: posted.append(f) or {},
                               now=lambda: 5000)
    ok, note = cli.join("11", "22", allow_consent=True)
    assert ok is False
    assert "invalid_scope" in note, note          # Discord's real reason is surfaced
    assert posted == []                            # the numeric 5000 was NOT exchanged


def t_default_post_form_surfaces_discord_error_body():
    # A 400 from Discord's token endpoint must surface the response body's reason
    # (e.g. a missing http://localhost redirect, or a bad client secret) instead of a
    # bare "HTTP Error 400: Bad Request", which tells the operator nothing. Discord
    # never echoes the client secret, so surfacing the body is safe.
    import io, urllib.error
    body = (b'{"error":"invalid_request",'
            b'"error_description":"Invalid \\"redirect_uri\\" in request."}')

    def boom(url, **kw):
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body))

    orig = m.http_util.open_url
    m.http_util.open_url = boom
    try:
        raised = None
        try:
            m._default_post_form(m.TOKEN_URL, {"grant_type": "authorization_code"})
        except RuntimeError as exc:
            raised = str(exc)
        assert raised is not None, "expected RuntimeError carrying the body"
        assert "400" in raised and "redirect_uri" in raised, raised
    finally:
        m.http_util.open_url = orig


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

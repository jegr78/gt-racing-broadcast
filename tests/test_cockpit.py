#!/usr/bin/env python3
"""Stdlib unit checks for the Commentator Cockpit. Run: python3 tests/test_cockpit.py"""
import importlib.util
import json
import os
import re
import sys
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
cad = _load("console_admin", ("src", "scripts", "console_admin.py"))

SECRET = "test-secret-do-not-ship"
sys.path.insert(0, HERE)
import test_driver_tower as tower  # noqa: E402
COCKPIT = os.path.join(ROOT, "src", "cockpit", "cockpit.html")


def t_streamer_key_normalizes():
    assert ca.streamer_key("Alpha Racing") == "alpha-racing"
    assert ca.streamer_key("  Beta!#1  ") == "beta1"
    assert ca.streamer_key("") == ""
    assert ca.streamer_key(None) == ""


def t_mint_token_shape():
    tok = ca.mint_token(SECRET, "alpha-racing", version=1)
    key, ver, sig = tok.split(".")
    assert key == "alpha-racing"
    assert ver == "1"
    assert len(sig) == 32 and all(c in "0123456789abcdef" for c in sig)


def t_verify_round_trip():
    tok = ca.mint_token(SECRET, "alpha-racing")
    assert ca.verify_token(SECRET, tok) == "alpha-racing"


def t_verify_rejects_tampered_sig():
    tok = ca.mint_token(SECRET, "alpha-racing")
    bad = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    assert ca.verify_token(SECRET, bad) is None


def t_verify_rejects_wrong_secret():
    tok = ca.mint_token(SECRET, "alpha-racing")
    assert ca.verify_token("other-secret", tok) is None


def t_secret_matches():
    assert ca.secret_matches("abc", "abc") is True
    assert ca.secret_matches("abc", "abcd") is False
    assert ca.secret_matches("", "abc") is False
    assert ca.secret_matches(None, "abc") is False
    assert ca.secret_matches(None, None) is True   # both empty -> trivially equal


def t_verify_rejects_malformed():
    for bad in ("", "a.b", "a.b.c.d", "alpha.notint.deadbeef" + "0" * 24,
                "BADKEY.1." + "0" * 32, "alpha-racing.1.short"):
        assert ca.verify_token(SECRET, bad) is None, bad


def t_streamer_key_matches_asset_key():
    """console_auth.streamer_key must behave identically to relay asset_key()."""
    for s in ("Alpha Racing", "  Beta!#1 ", "Ümlaut x", "a-b_c d", "", "  "):
        assert ca.streamer_key(s) == m.asset_key(s), s


def t_verify_token_version_check():
    tok_v1 = ca.mint_token(SECRET, "alpha", version=1)
    assert ca.verify_token(SECRET, tok_v1, {"alpha": 1}) == "alpha"
    assert ca.verify_token(SECRET, tok_v1, {"alpha": 2}) is None   # stale version
    assert ca.verify_token(SECRET, tok_v1, {}) == "alpha"          # default 1
    tok_v2 = ca.mint_token(SECRET, "alpha", version=2)
    assert ca.verify_token(SECRET, tok_v2, {"alpha": 2}) == "alpha"


def t_safe_cookie_token_allowlist():
    # A real minted token passes through unchanged.
    tok = ca.mint_token(SECRET, "alpha-racing", version=2)
    assert ca.safe_cookie_token(tok) == tok
    # CR/LF (response splitting), ';' (cookie attribute injection), spaces and the
    # empty/None inputs all collapse to "" so nothing unsafe reaches Set-Cookie.
    assert ca.safe_cookie_token("a.1.deadbeef\r\nSet-Cookie: evil=1") == ""
    assert ca.safe_cookie_token("a.1.x; HttpOnly=no") == ""
    assert ca.safe_cookie_token("a b") == ""
    assert ca.safe_cookie_token("") == ""
    assert ca.safe_cookie_token(None) == ""


def t_rate_limiter_fixed_window():
    rl = ca.RateLimiter(limit=2, window_s=60)
    assert rl.allow("ip", now=0) is True
    assert rl.allow("ip", now=1) is True
    assert rl.allow("ip", now=2) is False      # 3rd hit in window -> blocked
    assert rl.allow("other", now=2) is True    # counter is per-key
    assert rl.allow("ip", now=61) is True       # window reset


def t_rate_limiter_key_map_stays_bounded():
    rl = ca.RateLimiter(limit=1, window_s=60, max_keys=3)
    for i in range(3):
        assert rl.allow(f"k{i}", now=0)
    assert not rl.allow("k2", now=1), "a live window still counts"
    assert rl.allow("new", now=61) and len(rl._hits) == 1, "expired windows leave the map first"
    for i in range(10):
        rl.allow(f"x{i}", now=62)
    assert len(rl._hits) == 3, f"never more than max_keys: {len(rl._hits)}"


def t_versions_default_and_bump():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "console-versions.json")
        assert cad.load_versions(p) == {}                 # missing -> {}
        assert cad.current_version({}, "alpha") == 1      # default 1
        assert cad.bump_version(p, "alpha") == 2          # 1 -> 2, persisted
        assert cad.load_versions(p) == {"alpha": 2}
        assert cad.bump_version(p, "alpha") == 3


def t_revoked_token_rejected_after_bump():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "console-versions.json")
        tok_v1 = ca.mint_token(SECRET, "alpha", version=1)
        assert ca.verify_token(SECRET, tok_v1, cad.load_versions(p)) == "alpha"
        cad.bump_version(p, "alpha")                       # now current = 2
        assert ca.verify_token(SECRET, tok_v1, cad.load_versions(p)) is None
        tok_v2 = ca.mint_token(SECRET, "alpha", version=2)
        assert ca.verify_token(SECRET, tok_v2, cad.load_versions(p)) == "alpha"


def t_apply_pulled_validates():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "console-versions.json")
        assert cad.apply_pulled(p, {"versions": {"alpha": 3, "beta": 2}}) == 2
        assert cad.load_versions(p) == {"alpha": 3, "beta": 2}
        for bad in ({"versions": {"alpha": 0}}, {"versions": {"BAD KEY": 2}},
                    {"versions": {"alpha": "x"}}, {"nope": {}}, []):
            try:
                cad.apply_pulled(p, bad)
                raise AssertionError(f"expected ValueError for {bad!r}")
            except ValueError:
                pass  # expected: bad payload rejected before any write


def t_console_link_discord_payload_basics():
    p = cad.console_link_discord_payload("https://h.ts.net/console", "")
    # mirrors the other racecast posts: same identity, @here in top-level content
    # (Discord ignores mentions inside an embed), the link in a titled embed
    assert p["username"] == "GT Racecast"
    assert p["content"] == "@here"
    assert p["allowed_mentions"] == {"parse": ["everyone"]}
    embed = p["embeds"][0]
    assert embed["title"] == "\U0001F399️ Crew Console"
    assert "https://h.ts.net/console" in embed["description"]
    # no league -> no footer leaked, no placeholder text anywhere in the embed
    assert "footer" not in embed
    assert "None" not in embed["description"]


def t_console_link_discord_payload_weaves_league_name():
    p = cad.console_link_discord_payload("https://h.ts.net/console", "GT Masters")
    # a non-empty league shows as the embed footer (like event_title elsewhere)
    assert p["embeds"][0]["footer"] == {"text": "GT Masters"}


def _rows():
    # ScheduleSource 4-tuples: (url, streamer, stint, line)
    return [("u0", "Alpha Racing", "S1", 2),
            ("u1", "Beta", "S2", 3),
            ("u2", "Alpha Racing", "S3", 4),
            ("u3", "Gamma", "S4", 5)]


def t_tally_on_air():
    t = m.cockpit_tally(_rows(), 0, "alpha-racing")
    assert t["on_air"] is True
    assert t["up_next"] == {"stint": "S3", "in_n": 2}
    assert t["scheduled"] is True


def t_tally_up_next_only():
    t = m.cockpit_tally(_rows(), 0, "beta")
    assert t["on_air"] is False
    assert t["up_next"] == {"stint": "S2", "in_n": 1}
    assert t["scheduled"] is True


def t_tally_live_idx_none():
    # No feed on air yet: no on_air, the loop is skipped, but me is scheduled.
    t = m.cockpit_tally(_rows(), None, "alpha-racing")
    assert t == {"on_air": False, "up_next": None, "scheduled": True}


def t_tally_not_upcoming():
    t = m.cockpit_tally(_rows(), 2, "beta")     # Beta already passed
    assert t["on_air"] is False and t["up_next"] is None and t["scheduled"] is True


def t_tally_not_scheduled():
    t = m.cockpit_tally(_rows(), 0, "nobody")
    assert t == {"on_air": False, "up_next": None, "scheduled": False}


def t_display_name_maps_key_to_name():
    assert m.cockpit_display_name(_rows(), "alpha-racing") == "Alpha Racing"
    assert m.cockpit_display_name(_rows(), "nobody") == "nobody"


def t_race_control_schedule_redacts_url():
    # The Race Control desk (#244) sees stint + streamer + a live-feed marker, but
    # NEVER a stream URL, the same redaction boundary as /console/takeover/status.
    sched = m.race_control_schedule(_rows(), {0: "A", 1: "B"}, 1)
    assert sched == [
        {"stint": "S1", "streamer": "Alpha Racing", "live": "A", "on_air": False},
        {"stint": "S2", "streamer": "Beta", "live": "B", "on_air": True},
        {"stint": "S3", "streamer": "Alpha Racing", "live": None, "on_air": False},
        {"stint": "S4", "streamer": "Gamma", "live": None, "on_air": False}], \
        "only the feed on air is on air; the other loaded stint is not"
    # No row carries a URL key, and no value leaks the source URL.
    for row in sched:
        assert "url" not in row, row
    assert "u0" not in json.dumps(sched) and "http" not in json.dumps(sched).lower()


def t_race_control_schedule_empty():
    assert m.race_control_schedule([], {}, None) == []


def t_cockpit_schedule_flags_on_air_and_mine():
    # me = "alpha-racing"; on-air feed is row 1 (Beta). Alpha's rows (0, 2) are
    # "mine"; only row 1 is on_air.
    sched = m.cockpit_schedule(_rows(), 1, "alpha-racing")
    assert sched == [
        {"stint": "S1", "streamer": "Alpha Racing", "on_air": False, "mine": True},
        {"stint": "S2", "streamer": "Beta", "on_air": True, "mine": False},
        {"stint": "S3", "streamer": "Alpha Racing", "on_air": False, "mine": True},
        {"stint": "S4", "streamer": "Gamma", "on_air": False, "mine": False}], sched


def t_cockpit_schedule_redacts_url():
    # Reachable over the Funnel -> never a stream URL (the takeover redaction line).
    sched = m.cockpit_schedule(_rows(), 0, "beta")
    for row in sched:
        assert "url" not in row, row
    assert "u0" not in json.dumps(sched) and "http" not in json.dumps(sched).lower()


def t_cockpit_schedule_live_idx_none():
    # No feed on air yet -> no row is on_air; mine flags still resolve.
    sched = m.cockpit_schedule(_rows(), None, "alpha-racing")
    assert all(r["on_air"] is False for r in sched)
    assert [r["mine"] for r in sched] == [True, False, True, False]


def t_cockpit_schedule_empty():
    assert m.cockpit_schedule([], 0, "anyone") == []


def _seed_graphics(d):
    """Write a few dummy PNGs (+ one non-PNG) into dir d; return it."""
    for fn in ("Standings.png", "Schedule.png", "Race Results.png", "notes.txt"):
        with open(os.path.join(d, fn), "wb") as fh:
            fh.write(b"\x89PNG\r\n" + fn.encode())
    return d


def t_list_graphics_sorted_pngs_only():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        got = m.list_graphics(d)
        assert got == [
            {"name": "Race Results", "file": "Race Results.png"},
            {"name": "Schedule", "file": "Schedule.png"},
            {"name": "Standings", "file": "Standings.png"},
        ], got


def t_list_graphics_missing_or_unset_dir_is_empty():
    assert m.list_graphics(None) == []
    assert m.list_graphics("/no/such/dir/xyz") == []


def t_list_graphics_filters_internal_from_manifest():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)  # Standings, Schedule, Race Results (+ notes.txt)
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"internal": ["Schedule"]}, fh)
        got = m.list_graphics(d)
        assert got == [
            {"name": "Race Results", "file": "Race Results.png"},
            {"name": "Standings", "file": "Standings.png"},
        ], got  # Schedule hidden; manifest.json itself never listed (not a .png)


def t_list_graphics_internal_match_is_case_insensitive():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"internal": ["  sTaNdInGs "]}, fh)  # padded + mixed case
        got = m.list_graphics(d)
        assert [e["name"] for e in got] == ["Race Results", "Schedule"], got


def t_list_graphics_absent_or_malformed_manifest_shows_all():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        assert len(m.list_graphics(d)) == 3, "no manifest -> all listed"
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        assert len(m.list_graphics(d)) == 3, "malformed manifest -> all listed"
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"internal": "Schedule"}, fh)  # wrong type (str, not list)
        assert len(m.list_graphics(d)) == 3, "bad internal type -> all listed"


def t_list_graphics_excludes_placeholder_pngs():
    # A pure-placeholder graphic (transparent PNG seeded/reset for an un-linked or
    # un-Sheeted asset, #387) renders blank -> it must NOT appear in the browser.
    import shutil
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)  # Standings, Schedule, Race Results (real-ish PNGs)
        shutil.copyfile(m.placeholders.graphic_placeholder_path(),
                        os.path.join(d, "Weather Rain.png"))  # byte-identical placeholder
        names = [e["name"] for e in m.list_graphics(d)]
        assert "Weather Rain" not in names, names
        assert names == ["Race Results", "Schedule", "Standings"], names


def t_resolve_graphic_happy_path():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        hit = m.resolve_graphic(d, "Race Results.png")
        assert hit is not None
        path, ctype = hit
        assert ctype == "image/png"
        assert os.path.basename(path) == "Race Results.png"
        assert os.path.realpath(path).startswith(os.path.realpath(d) + os.sep)


def t_resolve_graphic_rejects_traversal_and_non_png():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        for bad in ("../secret.png", "a/b.png", "a\\b.png", "..", ".",
                    "notes.txt", "Missing.png", "", "/etc/passwd"):
            assert m.resolve_graphic(d, bad) is None, bad
        assert m.resolve_graphic(None, "Standings.png") is None


class _FakeCrew:
    """Minimal CrewSource stand-in for role resolution in the HTTP harness:
    get() yields the 3-tuple view resolve_roles unpacks; the *_keys() helpers
    drive commentator / race_control membership."""
    def __init__(self, rows=(), commentator_keys=(), race_control_keys=()):
        self._rows = list(rows)
        self._ck = frozenset(commentator_keys)
        self._rck = frozenset(race_control_keys)

    def get(self):
        return list(self._rows)

    def commentator_keys(self):
        return self._ck

    def race_control_keys(self):
        return self._rck

    def discord_map(self):
        return {}


def _cockpit_client(secret="sek", rows=None, live_idx=0,
                    versions_path=None, chat_store=None, timer_store=None,
                    page_path=None, graphics_dir=None, app_version="v9.9.9-test",
                    console_page_path=None, discord_client_id=None,
                    discord_client_secret=None, preview_manager=None,
                    cue_store=None, crew_source=None,
                    program_audio_service=None, fanout=False, backlogged=None,
                    mode="race", race_rows=None):
    """Stand up make_handler over a real ThreadingHTTPServer on an ephemeral port.
    Returns (server, get, post); caller must srv.shutdown() in a finally block."""
    import threading as _t
    import urllib.error
    from urllib.request import Request, urlopen

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
            self.race_source = _Source(race_rows) if race_rows is not None else self.source
            self.mode = mode
            self.feeds = {"A": _Feed(live_idx), "B": _Feed(live_idx + 1)}
            self.fanout = fanout
            self._desync = {"active": False}
            self._backlogged_feeds = dict(backlogged or {})

        def live_feed(self):
            return "A"

        def on_air_row_idx(self):
            return self.feeds[self.live_feed()].idx

        def live_row_map(self):
            return {f.idx: k for k, f in self.feeds.items()}

        def status(self):
            return {"schedule_len": len(self.source.get_rows())}

    handler = m.make_handler(_Relay(), chat_store=chat_store, timer_store=timer_store,
                             cockpit_page_path=page_path, console_secret=secret,
                             app_version=app_version,
                             console_versions_path=versions_path,
                             graphics_dir=graphics_dir,
                             console_page_path=console_page_path,
                             discord_client_id=discord_client_id,
                             discord_client_secret=discord_client_secret,
                             preview_manager=preview_manager,
                             cue_store=cue_store, crew_source=crew_source,
                             program_audio_service=program_audio_service)
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

    return srv, get, post


def t_data_requires_auth():
    srv, get, _post = _cockpit_client()
    try:
        code, _h, _b = get("/cockpit/data")
        assert code == 401, code
    finally:
        srv.shutdown()


def t_data_without_secret_is_404():
    # No CONSOLE_SECRET configured -> cockpit not served (every /cockpit/* 404s).
    srv, get, _post = _cockpit_client(secret=None)
    try:
        tok = ca.mint_token("sek", "alpha-racing")
        code, _h, _b = get("/cockpit/data?t=" + tok)
        assert code == 404, code
    finally:
        srv.shutdown()


def t_data_authed_tally():
    srv, get, _post = _cockpit_client()
    try:
        tok = ca.mint_token("sek", "alpha-racing")
        code, _h, body = get("/cockpit/data?t=" + tok)
        assert code == 200, code
        d = json.loads(body)
        assert d["me"] == "alpha-racing" and d["on_air"] is True
        assert d["up_next"] == {"stint": "S3", "in_n": 2}
        assert d["mode"] == "race"
        # my_stints surfaces the commentator's OWN urls (u0, u2) so the cockpit
        # can pre-fill them, but never a foreign row's url (u1 Beta, u3 Gamma)
        # or raw relay status.
        assert "u1" not in body.decode() and "u3" not in body.decode()
        assert "schedule_len" not in body.decode()
    finally:
        srv.shutdown()


def t_cockpit_race_stints_only_in_qualifying_and_without_urls():
    race = [("u0", "Alpha Racing", "S1", 2), ("", "Alpha Racing", "S3", 4), ("u9", "Beta", "S2", 3)]
    assert m.cockpit_race_stints("race", race, "alpha-racing") == [], \
        "in a race the cockpit's own stints are the race stints already"
    got = m.cockpit_race_stints("qualifying", race, "alpha-racing")
    assert got == [{"row": 1, "stint": "S1", "has_link": True}, {"row": 2, "stint": "S3", "has_link": False}], got


def t_data_in_qualifying_carries_the_own_race_stints():
    quali = [("q0", "Alpha Racing", "Qualifying", 2)]
    race = [("u0", "Alpha Racing", "S1", 2), ("", "Alpha Racing", "S3", 4), ("u9", "Beta", "S2", 3)]
    srv, get, _post = _cockpit_client(rows=quali, race_rows=race, mode="qualifying")
    try:
        code, _h, body = get("/cockpit/data?t=" + ca.mint_token("sek", "alpha-racing"))
        assert code == 200, code
        d = json.loads(body)
        assert d["race_stints"] == [{"row": 1, "stint": "S1", "has_link": True},
                                    {"row": 2, "stint": "S3", "has_link": False}], d.get("race_stints")
        assert "u0" not in body.decode() and "u9" not in body.decode(), "race stint links never leave the relay"
    finally:
        srv.shutdown()


def t_data_program_behind_only_above_the_threshold():
    # The commentator hears a delayed program against their own voice. The cockpit
    # says so only when the relay classified an output backlog, never as a
    # permanent seconds counter. (#583)
    tok = ca.mint_token("sek", "alpha-racing")
    srv, get, _post = _cockpit_client()
    try:
        d = json.loads(get("/cockpit/data?t=" + tok)[2])
        assert d["program_behind_s"] is None
    finally:
        srv.shutdown()
    srv, get, _post = _cockpit_client(backlogged={"A": 11.6})
    try:
        d = json.loads(get("/cockpit/data?t=" + tok)[2])
        assert d["program_behind_s"] == 12
    finally:
        srv.shutdown()


def t_cockpit_program_behind_pure():
    assert m.cockpit_program_behind(None) is None
    assert m.cockpit_program_behind({}) is None
    assert m.cockpit_program_behind({"A": 9.4}) == 9
    assert m.cockpit_program_behind({"A": 9.4, "POV": 14.6}) == 15


def t_rc_note_send_and_cockpit_read_round_trip():
    # RC -> commentator notes (#376): a race_control subject sends a note to the
    # on-air commentator over /console; the commentator reads it from the rolling
    # cockpit RC card, and it does NOT leak into the director-cue stream.
    with tempfile.TemporaryDirectory() as d:
        store = m.CueStore(os.path.join(d, "cues.json"))
        crew = _FakeCrew(race_control_keys={"rcdesk"})
        srv, get, post = _cockpit_client(cue_store=store, crew_source=crew)
        try:
            rc_tok = ca.mint_token("sek", "rcdesk")
            code, _h, body = post("/console/race-control/cues?t=" + rc_tok,
                                  {"target": "on-air", "text": "team 5 DC — rejoin next stint"})
            assert code == 200, (code, body)
            r = json.loads(body)
            assert r["ok"] and r["cue"]["origin"] == "race_control"
            assert r["cue"]["from"] == "Race Control"
            # The on-air commentator (Alpha Racing, schedule -> commentator) reads it.
            ct = ca.mint_token("sek", "alpha-racing")
            code, _h, body = get("/cockpit/rc-notes?t=" + ct)
            assert code == 200, code
            notes = json.loads(body)["notes"]
            assert [n["text"] for n in notes] == ["team 5 DC — rejoin next stint"]
            # ...and it never appears as a director cue.
            code, _h, body = get("/cockpit/cues?t=" + ct)
            assert json.loads(body)["cues"] == []
        finally:
            srv.shutdown()


def t_rc_note_send_forbidden_for_commentator():
    # A bare commentator may not drive the RC -> commentator channel.
    with tempfile.TemporaryDirectory() as d:
        store = m.CueStore(os.path.join(d, "cues.json"))
        crew = _FakeCrew()           # alpha-racing has no race_control flag
        srv, _get, post = _cockpit_client(cue_store=store, crew_source=crew)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            code, _h, _b = post("/console/race-control/cues?t=" + tok,
                                {"target": "all", "text": "nope"})
            assert code == 403, code
            assert store.list() == []
        finally:
            srv.shutdown()


def t_cue_back_round_trip_and_identity_forced():
    # Commentator -> director cue-back (#377): the commentator posts; it surfaces
    # on the Director Panel feed (/cues/back) tagged with the token's display name,
    # and never leaks into the commentator's own cockpit cues/RC notes.
    with tempfile.TemporaryDirectory() as d:
        store = m.CueStore(os.path.join(d, "cues.json"))
        srv, get, post = _cockpit_client(cue_store=store)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            code, _h, body = post("/console/cockpit/cue-back?t=" + tok,
                                  {"text": "ready in 2", "from": "spoofed"})
            assert code == 200, (code, body)
            r = json.loads(body)
            assert r["ok"] and r["cue"]["origin"] == "commentator"
            assert r["cue"]["from"] == "Alpha Racing"   # identity forced, not "spoofed"
            # Director Panel read (root /cues/back is tailnet-trusted, no auth).
            code, _h, body = get("/cues/back")
            assert code == 200, code
            backs = json.loads(body)["cueBacks"]
            assert [(c["from"], c["text"]) for c in backs] == [("Alpha Racing", "ready in 2")]
            # Not echoed back into the sender's own cockpit surfaces.
            code, _h, body = get("/cockpit/cues?t=" + tok)
            assert json.loads(body)["cues"] == []
            code, _h, body = get("/cockpit/rc-notes?t=" + tok)
            assert json.loads(body)["notes"] == []
        finally:
            srv.shutdown()


def t_page_sets_cookie_and_serves_html():
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "cockpit.html")
        with open(page, "w") as fh:
            fh.write("<!doctype html><title>cockpit</title>")
        srv, get, _post = _cockpit_client(page_path=page)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            code, headers, body = get("/cockpit?t=" + tok)
            assert code == 200, code
            assert b"cockpit" in body
            setc = headers.get("Set-Cookie", "")
            assert "rc_console=" in setc and "HttpOnly" in setc and "SameSite=Lax" in setc
            # plain-http (tailnet) request -> NO Secure, else the browser drops it
            assert "Secure" not in setc
            # #351: an img-src CSP allowlists the broadcast-chat emote CDNs;
            # only img-src is set so inline scripts/styles stay unaffected.
            # (dotless fragments below dodge the URL-substring CodeQL heuristic)
            csp = headers.get("Content-Security-Policy", "")
            assert "img-src" in csp and "jtvnw" in csp
            assert "ggpht" in csp and "default-src" not in csp
        finally:
            srv.shutdown()


def t_page_substitutes_version():
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "cockpit.html")
        with open(page, "w") as fh:
            fh.write("<!doctype html><title>cockpit</title><span>__RC_VERSION__</span>")
        srv, get, _post = _cockpit_client(page_path=page, app_version="v9.9.9-test")
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            code, _headers, body = get("/cockpit?t=" + tok)
            assert code == 200, code
            assert b"v9.9.9-test" in body, body
            assert b"__RC_VERSION__" not in body, body   # placeholder fully replaced
        finally:
            srv.shutdown()


def t_page_cookie_secure_behind_funnel():
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "cockpit.html")
        with open(page, "w") as fh:
            fh.write("<!doctype html><title>cockpit</title>")
        srv, get, _post = _cockpit_client(page_path=page)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            code, headers, _body = get("/cockpit?t=" + tok,
                                       headers={"X-Forwarded-Proto": "https"})
            assert code == 200, code
            assert "Secure" in headers.get("Set-Cookie", "")   # Funnel HTTPS -> Secure
        finally:
            srv.shutdown()


def t_page_bad_token_401_no_cookie():
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "cockpit.html")
        open(page, "w").close()
        srv, get, _post = _cockpit_client(page_path=page)
        try:
            code, headers, _b = get("/cockpit?t=bogus")
            assert code == 401, code
            assert "rc_console=" not in (headers.get("Set-Cookie") or "")
        finally:
            srv.shutdown()


def _launcher_page(d):
    page = os.path.join(d, "console.html")
    with open(page, "w") as fh:
        fh.write("<!doctype html><title>launcher</title>__RC_OAUTH__")
    return page


def t_console_page_login_fallback_on_invalid_token():
    # A human /console PAGE GET with a MISSING or INVALID token, and OAuth
    # configured, must serve the launcher (Login with Discord), NEVER a naked 401
    # JSON page. A stale cookie from another profile must not dead-end.
    with tempfile.TemporaryDirectory() as d:
        page = _launcher_page(d)
        srv, get, _post = _cockpit_client(console_page_path=page,
                                          discord_client_id="cid",
                                          discord_client_secret="csec")
        try:
            for path in ("/console", "/console/health-monitor", "/console/cockpit",
                         "/console/panel", "/console/race-control"):
                code, headers, body = get(path, cookie="rc_console=someone.1.deadbeef")
                assert code == 200, (path, code)
                assert b"launcher" in body, path
                # the bad token must NOT mint a session cookie
                assert "rc_console=" not in (headers.get("Set-Cookie") or ""), path
            # and with no token at all
            code, _h, body = get("/console")
            assert code == 200 and b"launcher" in body, code
        finally:
            srv.shutdown()


def t_console_data_endpoint_still_401_on_invalid_token():
    # Only human PAGE GETs fall back to the launcher; API/data/identity routes stay
    # hard-gated (401) even with OAuth configured, so no data leaks to a bad token.
    with tempfile.TemporaryDirectory() as d:
        page = _launcher_page(d)
        srv, get, _post = _cockpit_client(console_page_path=page,
                                          discord_client_id="cid",
                                          discord_client_secret="csec")
        try:
            for path in ("/console/health-monitor/data", "/console/whoami"):
                code, _h, _b = get(path, cookie="rc_console=someone.1.deadbeef")
                assert code == 401, (path, code)
        finally:
            srv.shutdown()


def t_console_page_401_without_oauth():
    # No OAuth configured -> no login page exists, so an invalid token on a page GET
    # stays 401 (signed links remain the only entry path on OAuth-off installs).
    with tempfile.TemporaryDirectory() as d:
        page = _launcher_page(d)
        srv, get, _post = _cockpit_client(console_page_path=page)   # no discord_client_*
        try:
            code, _h, _b = get("/console", cookie="rc_console=someone.1.deadbeef")
            assert code == 401, code
        finally:
            srv.shutdown()


def t_timer_authed():
    class _Timer:
        def data(self):
            return {"running": False, "remaining": "1:00:00"}
    srv, get, _post = _cockpit_client(timer_store=_Timer())
    try:
        tok = ca.mint_token("sek", "alpha-racing")
        code, _h, body = get("/cockpit/timer", cookie="rc_console=" + tok)
        assert code == 200, code
        assert json.loads(body)["remaining"] == "1:00:00"
    finally:
        srv.shutdown()


def t_cockpit_chat_send_forces_identity():
    with tempfile.TemporaryDirectory() as d:
        cs = m.ChatStore(os.path.join(d, "chat.json"))
        srv, get, post = _cockpit_client(chat_store=cs)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            # client tries to spoof "user" -> must be ignored, forced to display name
            code, _h, body = post("/cockpit/chat/send",
                                  {"user": "Impostor", "text": "hi"},
                                  cookie="rc_console=" + tok)
            assert code == 200, (code, body)
            assert json.loads(body)["message"]["user"] == "Alpha Racing"
            code, _h, body = get("/cockpit/chat/data", cookie="rc_console=" + tok)
            msgs = json.loads(body)["messages"]
            assert msgs[-1]["user"] == "Alpha Racing" and msgs[-1]["text"] == "hi"
        finally:
            srv.shutdown()


def t_cockpit_chat_requires_auth():
    with tempfile.TemporaryDirectory() as d:
        cs = m.ChatStore(os.path.join(d, "chat.json"))
        srv, get, post = _cockpit_client(chat_store=cs)
        try:
            assert get("/cockpit/chat/data")[0] == 401
            assert post("/cockpit/chat/send", {"text": "x"})[0] == 401
        finally:
            srv.shutdown()


def t_versions_endpoint_requires_secret():
    with tempfile.TemporaryDirectory() as d:
        vp = os.path.join(d, "console-versions.json")
        cad.write_versions(vp, {"alpha": 3})
        srv, get, _post = _cockpit_client(secret="sek", versions_path=vp)
        try:
            # /cockpit/versions sits under the funnelled /cockpit prefix -> must auth
            assert get("/cockpit/versions")[0] == 401                       # no header
            assert get("/cockpit/versions",
                       headers={"X-Console-Secret": "wrong"})[0] == 401     # bad secret
            code, _h, body = get("/cockpit/versions",
                                 headers={"X-Console-Secret": "sek"})       # right secret
            assert code == 200 and json.loads(body)["versions"] == {"alpha": 3}
        finally:
            srv.shutdown()


def t_graphics_list_requires_auth():
    srv, get, _post = _cockpit_client()
    try:
        assert get("/cockpit/graphics")[0] == 401
    finally:
        srv.shutdown()


def t_graphics_list_authed_sorted():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        srv, get, _post = _cockpit_client(graphics_dir=d)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            code, _h, body = get("/cockpit/graphics?t=" + tok)
            assert code == 200, code
            names = [e["name"] for e in json.loads(body)["graphics"]]
            assert names == ["Race Results", "Schedule", "Standings"], names
        finally:
            srv.shutdown()


def t_graphics_list_empty_without_dir():
    srv, get, _post = _cockpit_client()          # graphics_dir=None
    try:
        tok = ca.mint_token("sek", "alpha-racing")
        code, _h, body = get("/cockpit/graphics?t=" + tok)
        assert code == 200 and json.loads(body)["graphics"] == [], body
    finally:
        srv.shutdown()


def t_graphic_file_served_with_png_ctype():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        srv, get, _post = _cockpit_client(graphics_dir=d)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            # %20 in the path exercises the unquote() of the filename segment.
            code, headers, body = get("/cockpit/graphics/Race%20Results.png?t=" + tok)
            assert code == 200, code
            assert headers["Content-Type"] == "image/png", headers["Content-Type"]
            with open(os.path.join(d, "Race Results.png"), "rb") as fh:
                assert body == fh.read()
        finally:
            srv.shutdown()


def t_graphic_file_requires_auth():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        srv, get, _post = _cockpit_client(graphics_dir=d)
        try:
            assert get("/cockpit/graphics/Standings.png")[0] == 401
        finally:
            srv.shutdown()


def t_graphic_file_traversal_and_missing_are_404():
    with tempfile.TemporaryDirectory() as d:
        _seed_graphics(d)
        srv, get, _post = _cockpit_client(graphics_dir=d)
        try:
            tok = ca.mint_token("sek", "alpha-racing")
            assert get("/cockpit/graphics/Missing.png?t=" + tok)[0] == 404
            assert get("/cockpit/graphics/notes.txt?t=" + tok)[0] == 404
            # URL-encoded traversal: %2F is NOT split into segments, then unquoted
            # to a slash and rejected by resolve_graphic.
            assert get("/cockpit/graphics/..%2Fsecret.png?t=" + tok)[0] == 404
        finally:
            srv.shutdown()


def t_console_preview_levels_any_auth():
    """GET /console/preview/levels is reachable by any authenticated /console
    subject (any-auth), and is denied without a valid token."""
    import logging as _logging

    class _MinRelay:
        """Minimal stub: PreviewManager.__init__ stores relay but levels()
        returns {} immediately when no pull worker is active, calling nothing."""

    pm = m.PreviewManager(_MinRelay(), lambda: None, _logging.getLogger("test"))
    srv, get, _post = _cockpit_client(preview_manager=pm)
    try:
        tok = ca.mint_token("sek", "alpha-racing")
        # Authenticated: must be 200 with a JSON body.
        code, _h, body = get("/console/preview/levels", cookie="rc_console=" + tok)
        assert code == 200, code
        json.loads(body)  # must be valid JSON (e.g. {})
        # Unauthenticated: must be denied (401), NOT 404 and NOT 200.
        code, _h, _b = get("/console/preview/levels")
        assert code == 401, code
    finally:
        srv.shutdown()


def t_all_console_pages_strip_token_from_url():
    # Every /console page can be reached with `?t=<token>` (racecast links emits
    # `/console?t=…`); the response sets the rc_console cookie, so the page must
    # then strip the token from the address bar + history (no lingering auth token
    # in screen-shares / browser history). All token-bearing pages must keep this
    # in lock-step.
    pages = [
        ("console", "console.html"),          # the racecast-links launcher target
        ("cockpit", "cockpit.html"),
        ("console", "health-monitor.html"),
        ("racecontrol", "race-control.html"),
        ("director", "director-panel.html"),  # /console/panel?t=
        ("console", "buttons.html"),          # /console/buttons?t=
    ]
    for d, fn in pages:
        with open(os.path.join(ROOT, "src", d, fn), encoding="utf-8") as fh:
            page = fh.read()
        assert "history.replaceState" in page, f"{fn} does not strip the URL token"
        assert "location.pathname" in page, f"{fn} strip target is not the bare path"


def t_program_audio_probe_enforces_auth_before_availability():
    # Security-relevant sequencing: /cockpit/program-audio must run
    # _console_auth() BEFORE the ?probe=1 availability branch, so an
    # unauthenticated probe can never leak whether the feature is available.
    # Service present + fan-out on => an AUTHED probe returns 200 {"available": true};
    # the SAME probe WITHOUT a token is 401 and reveals nothing.
    srv, get, _post = _cockpit_client(program_audio_service=object(), fanout=True)
    try:
        code, _h, body = get("/cockpit/program-audio?probe=1")
        assert code == 401, code                      # auth rejected before the probe branch
        assert b"available" not in body, body         # no availability leak pre-auth
        tok = ca.mint_token("sek", "alpha-racing")
        code, _h, body = get("/cockpit/program-audio?probe=1&t=" + tok)
        assert code == 200, code                       # auth passes, probe answers
        assert json.loads(body).get("available") is True
    finally:
        srv.shutdown()


def t_program_audio_probe_authed_but_service_disabled_is_404():
    # Auth passes but no ProgramAudioService wired -> 404 (feature disabled),
    # confirming the ordering: auth first, THEN the program-audio gate.
    srv, get, _post = _cockpit_client(program_audio_service=None, fanout=True)
    try:
        tok = ca.mint_token("sek", "alpha-racing")
        code, _h, _b = get("/cockpit/program-audio?probe=1&t=" + tok)
        assert code == 404, code
    finally:
        srv.shutdown()


def t_cockpit_syncing_pure():
    assert m.cockpit_syncing({"active": True, "serving_feed": "B"}) is True
    assert m.cockpit_syncing({"active": False}) is False
    assert m.cockpit_syncing({}) is False       # missing key -> not syncing


def _page():
    with open(COCKPIT, encoding="utf-8") as fh:
        return fh.read()


def _between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i + 1)]


def t_the_cockpit_areas_match_the_nav_and_the_tab_bar():
    html = _page()
    areas = re.findall(r'class="navbtn" data-area="([a-z]+)"', html)
    assert areas == ["timing", "stint", "graphics", "notes"], f"Timing first, so it is the default area: {areas}"
    assert re.findall(r'<div class="area" data-area="([a-z]+)" data-tab="([a-z]+)">', html) == \
        [(a, a) for a in areas], "each area is the tab of the same name below 820 px"
    tabs = [t[0] for t in json.loads(re.search(r"data-tabs='([^']*)'", html).group(1))]
    assert tabs == ["live"] + areas + ["chat"], f"the tab bar maps onto the areas, Timing has its own tab: {tabs}"
    used = set(re.findall(r'data-tab="([a-z]+)"', html))
    assert used == set(tabs), f"every tab shows something and nothing hides behind an unknown tab: {used}"
    live = _between(html, '<section id="liveCol"', "</section>")
    assert html.index('id="cueCritical"') < html.index('<div class="frame wrap"'), \
        "a critical cue spans the page above the frame on a desktop"
    for cid in ("tally", "program", "pgmToggle", "pgmAudioBtn", "timer", "cbSend", "rcCard"):
        assert f'id="{cid}"' in live, f"#{cid} sits in the live column"
    rail = _between(html, '<aside class="rail"', "</aside>")
    assert rail.index('id="chatCard"') < rail.index('id="bchatCard"'), "crew chat on top, broadcast chat below"
    for cid, area in (("timingCard", "timing"), ("submitCard", "stint"), ("stintPlan", "stint"),
                      ("gfxList", "graphics"), ("eventInfoInline", "notes")):
        part = _between(html, f'<div class="area" data-area="{area}"', '<div class="area"' if area != "notes"
                        else "</main>")
        assert f'id="{cid}"' in part, f"#{cid} is in the {area} area"
    assert '<div class="frame wrap"' in html, "the crew frame's 44 px phone targets apply to the frame"


def _stint_dot(d):
    return tower._run(tower._function(COCKPIT, "stintDot") + "\n;console.log(JSON.stringify(stintDot("
                      + json.dumps(d) + ")));")


def t_my_stint_dot_marks_a_missing_or_pending_link():
    def data(on_air, stints, pending=(), enabled=True):
        rows = [{"stint": f"Stint {i + 1}", "on_air": i == on_air, "mine": i + 1 in [s[0] for s in stints]}
                for i in range(6)]
        return {"submit_enabled": enabled, "schedule": rows,
                "my_stints": [{"row": r, "stint": f"Stint {r}", "has_link": link, "url": "u" if link else ""}
                              for r, link in stints],
                "my_pending": [{"id": "x", "stint": f"Stint {r}"} for r in pending]}
    if _stint_dot(data(-1, [])) is None:
        return
    got = {
        "past stint without a link": _stint_dot(data(3, [(1, False), (5, True)])),
        "next stint without a link": _stint_dot(data(1, [(1, True), (3, False)])),
        "nothing on air": _stint_dot(data(-1, [(1, False)])),   # the relay's index is past the end
        "pending replacement": _stint_dot(data(1, [(3, True)], pending=[3])),
        "first link pending": _stint_dot(data(1, [(3, False)], pending=[3])),
        "all linked": _stint_dot(data(0, [(1, True), (3, True)])),
        "submissions off": _stint_dot(data(-1, [(1, False)], enabled=False)),
        "no stints": _stint_dot(data(-1, [])),
    }
    off = {"level": "", "why": ""}
    assert got == {
        "past stint without a link": off,
        "next stint without a link": {"level": "warn", "why": "Stint 3: link missing"},
        "nothing on air": off,
        "pending replacement": {"level": "warn", "why": "Stint 3: awaiting approval"},
        "first link pending": {"level": "warn", "why": "Stint 3: awaiting approval"},
        "all linked": off,
        "submissions off": off,
        "no stints": off,
    }, f"the dot shows only for an own stint still to come whose link is missing or awaits approval: {got}"
    quali = dict(data(0, [(1, True)]), race_stints=[{"row": 1, "stint": "Stint 1", "has_link": True},
                                                    {"row": 4, "stint": "Stint 4", "has_link": False}])
    assert _stint_dot(quali) == {"level": "warn", "why": "Stint 4: link missing"}, \
        f"in qualifying every own race stint is still to come: {_stint_dot(quali)}"
    quali["my_pending"] = [{"id": "x", "stint": "Stint 4"}]
    assert _stint_dot(quali) == {"level": "warn", "why": "Stint 4: awaiting approval"}, _stint_dot(quali)
    past = data(6, [(1, False), (3, False)])
    assert _stint_dot(past) == off, f"past the schedule's end no stint is still to come: {_stint_dot(past)}"
    missing_wins = _stint_dot(data(0, [(3, True), (5, False)], pending=[3]))
    assert missing_wins == {"level": "warn", "why": "Stint 5: link missing"}, \
        f"a missing link is named before a pending one: {missing_wins}"


def t_the_stint_dot_follows_a_good_poll_only():
    poll = _between(_page(), "async function pollTally()", "\n}\n")
    ok = poll[poll.index("try"):poll.index("catch")]
    assert "renderStintDot(d)" in ok and ok.index("renderStintDot(d)") < ok.index("renderSubmit(d)"), \
        "the dot updates on a good answer, before the submit form may return early, and a failed poll keeps it"


def t_a_hidden_preview_fetches_no_program_and_lasts_one_session():
    html = _page()
    poll = _between(html, "function pollProgram()", "\n}\n")
    assert poll.index("if (!previewShown())") < poll.index("new Image()"), "a hidden preview loads no program still"
    assert "sessionStorage.getItem(PV_KEY)" in html and "localStorage.setItem(PV_KEY" not in html, \
        "a hidden preview never survives into the next event's first load"
    toggle = _between(html, "$('pgmToggle').addEventListener", "});")
    assert "$('pgmAudioBtn').click()" in toggle, "hiding the preview stops the program audio"


def _composed(page, slow_after=None):
    """`page` as the relay serves it at the tailnet root; `slow_after` (a marker in the page's
    main script) inserts an external script there that the test answers late."""
    def src(*rel):
        with open(os.path.join(ROOT, "src", *rel), encoding="utf-8") as fh:
            return fh.read()
    html = src(*page)
    if slow_after:
        i = html.index("</script>", html.index(slow_after[0])) + len("</script>")
        html = html[:i] + f'<script src="{slow_after[1]}"></script>' + html[i:]
    for token, value in (("__RC_API_BASE__", ""), ("__RC_VERSION__", "dev"),
                         ("<!--__CREW_FRAME__-->", src("console", "crew-frame.html")),
                         ("<!--__EVENT_INFO__-->", src("console", "event-info.html"))):
        html = html.replace(token, value)
    return html


def _browser_run(fn, routes):
    """Runs `fn(pw, browser, serve)` with Chromium; None without Playwright or a browser."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  (Playwright unavailable; the cockpit page is measured with visual acceptance)")
        return None
    def serve(route):
        path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        if path == "slow.js":
            return route.continue_()
        got = routes.get(path)
        if callable(got):
            got = got()
        if isinstance(got, str):
            return route.fulfill(body=got, content_type="text/html")
        if got is None:
            return route.fulfill(status=404, body="{}", content_type="application/json")
        return route.fulfill(body=json.dumps(got), content_type="application/json")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001  no browser binary on this machine
            print(f"  (no Chromium: {type(exc).__name__})")
            return None
        try:
            return fn(browser, serve)
        finally:
            browser.close()


def _cockpit_data(**kw):
    d = {"me": "comms-1", "mode": "race", "on_air": False, "up_next": None, "scheduled": True,
         "submit_enabled": True, "schedule": [{"stint": "S1", "streamer": "Comms 1", "on_air": True, "mine": True},
                                              {"stint": "S2", "streamer": "Comms 1", "on_air": False, "mine": True}],
         "my_stints": [{"row": 1, "stint": "S1", "has_link": True, "url": "u"},
                       {"row": 2, "stint": "S2", "has_link": False, "url": ""}],
         "my_pending": [], "race_stints": []}
    d.update(kw)
    return d


def t_the_cockpit_page_runs_its_frame_before_a_poll_answers():
    data = _cockpit_data()
    slow = tower.SlowScript()
    page = _composed(("cockpit", "cockpit.html"), slow_after=("pollBroadcastChat();", slow.url))

    def run(browser, serve):
        tab = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        tab.on("pageerror", lambda e: errors.append(str(e) + " " + (e.stack or "")[:300]))
        tab.route(slow.base + "/**", serve)
        tab.goto(slow.base + "/cockpit", wait_until="load")
        tab.wait_for_timeout(200)   # before the 2 s poll: only the first answer can have set the dot
        return {"errors": errors, "why": tab.evaluate(
            "document.querySelector('.navbtn[data-area=\"stint\"] .nwhy').textContent")}
    try:
        got = _browser_run(run, {"cockpit": page, "cockpit/data": data})
    finally:
        slow.close()
    if got is None:
        return
    assert got == {"errors": [], "why": "S2: link missing"}, \
        f"a poll that answers while the page still loads finds the frame ready: {got}"


def t_a_chat_opened_on_a_phone_shows_and_follows_the_newest_message():
    msgs = {"messages": [{"ts": 1_800_000_000 + i, "user": "Director", "text": f"message {i}"} for i in range(40)]}
    more = {"n": 40}

    def chat():
        more["n"] += 1
        return {"messages": msgs["messages"] + [{"ts": 1_800_001_000 + more["n"], "user": "Director",
                                                 "text": f"message {more['n']}"}]}
    bmsgs = {"messages": [{"ts": 1_800_000_000 + i, "user": f"viewer{i}", "text": "hi", "source": "yt:abcd"}
                          for i in range(40)]}
    page = _composed(("cockpit", "cockpit.html"))

    def run(browser, serve):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        tab = ctx.new_page()
        tab.route("http://ck.test/**", serve)
        tab.goto("http://ck.test/cockpit")
        tab.wait_for_timeout(1500)
        tab.click('#crewTabs button[data-id="chat"]')
        tab.wait_for_timeout(300)
        first = tab.evaluate("""() => ['chatlog', 'bchatlog'].map(id => {
          const l = document.getElementById(id); return l.scrollHeight - l.scrollTop - l.clientHeight; })""")
        tab.wait_for_timeout(3500)
        later = tab.evaluate("""() => { const l = document.getElementById('chatlog');
          return [l.scrollHeight - l.scrollTop - l.clientHeight, l.lastElementChild.textContent]; }""")
        ctx.close()
        return {"first": first, "later": later}
    got = _browser_run(run, {"cockpit": page, "cockpit/data": _cockpit_data(), "cockpit/chat/data": chat,
                             "broadcast-chat/data": bmsgs})
    if got is None:
        return
    assert all(gap < 24 for gap in got["first"]), f"both chats open at their newest message: {got}"
    assert got["later"][0] < 24 and "message 4" in got["later"][1], f"the crew chat keeps following: {got}"


def t_the_stint_dot_updates_on_a_good_answer_and_survives_a_failed_poll():
    js = "\n".join(tower._function(COCKPIT, n) for n in ("stintDot", "renderStintDot")) + "\nasync " + \
        tower._function(COCKPIT, "pollTally")
    got = tower._run("""
var dots = [], answers = [%s, null], waits = [];
function setDot(a, l, w) { dots.push([a, l, w]); }
function renderTally() {} function renderStintPlan() {}
function renderSubmit() { throw new Error("the form returned early"); }
async function j() { const a = answers.shift(); if (!a) throw new Error("502"); return a; }
function setTimeout(fn, ms) { waits.push(ms); }
%s
(async () => { await pollTally(); await pollTally(); console.log(JSON.stringify({dots, waits})); })();
""" % (json.dumps(_cockpit_data()), js))
    if got is None:
        return
    assert got == {"dots": [["stint", "warn", "S2: link missing"]], "waits": [2000, 2000]}, \
        f"the dot follows a good answer even when the form stops early, and a failed poll keeps it: {got}"


def t_hide_stops_the_program_stills_and_audio_for_the_tab_session():
    page = _composed(("cockpit", "cockpit.html"))
    stills = {"n": 0}

    def program():
        stills["n"] += 1
        return None

    def run(browser, serve):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        tab = ctx.new_page()
        tab.route("http://ck.test/**", serve)
        tab.goto("http://ck.test/cockpit")
        tab.wait_for_selector("#pgmAudioBtn", state="visible")
        tab.evaluate("""() => { const au = document.getElementById('pgmAudio');
          Object.defineProperty(au, 'paused', {get: () => !window.playing});
          au.play = () => { window.playing = true; return Promise.resolve(); };
          au.pause = () => { window.playing = false; }; }""")
        tab.click("#pgmAudioBtn")
        playing = tab.evaluate("window.playing === true")
        tab.click("#pgmToggle")
        tab.wait_for_timeout(300)
        before = stills["n"]
        tab.wait_for_timeout(3500)
        hidden = {"stills": stills["n"] - before, "playing": tab.evaluate("window.playing"),
                  "pressed": tab.get_attribute("#pgmAudioBtn", "aria-pressed")}
        tab.reload()
        tab.wait_for_timeout(500)
        reloaded = tab.evaluate("document.getElementById('pgmBody').hidden")
        fresh = ctx.new_page()
        fresh.route("http://ck.test/**", serve)
        fresh.goto("http://ck.test/cockpit")
        fresh.wait_for_timeout(500)
        other_tab = fresh.evaluate("document.getElementById('pgmBody').hidden")
        keys = tab.evaluate("Object.keys(sessionStorage)")
        ctx.close()
        return {"playing": playing, "hidden": hidden, "reloaded": reloaded, "other_tab": other_tab, "keys": keys}
    got = _browser_run(run, {"cockpit": page, "cockpit/data": _cockpit_data(), "cockpit/program": program,
                             "cockpit/program-audio": {"ok": True}})
    if got is None:
        return
    assert got["playing"] and got["hidden"] == {"stills": 0, "playing": False, "pressed": "false"}, \
        f"a hidden preview loads no still and its audio stops: {got}"
    assert got["reloaded"] is True and got["other_tab"] is False, f"Hide lasts for this tab only: {got}"
    assert "cockpit_preview" in got["keys"] and "rc_preview" not in got["keys"], \
        f"the cockpit keeps its own Hide, apart from the desk's: {got}"


def t_a_stint_label_with_markup_stays_text_in_the_picker():
    label = '<img src=x onerror="window.pwned=1">S1'
    data = _cockpit_data(my_stints=[{"row": 1, "stint": label, "has_link": True, "url": "u"}])
    page = _composed(("cockpit", "cockpit.html"))

    def run(browser, serve):
        tab = browser.new_page(viewport={"width": 1440, "height": 900})
        tab.route("http://ck.test/**", serve)
        tab.goto("http://ck.test/cockpit")
        tab.wait_for_timeout(1500)
        return tab.evaluate("""() => { const sel = document.getElementById('subStint');
          return {options: [...sel.options].map(o => [o.value, o.textContent]), imgs: sel.querySelectorAll('img').length,
                  pwned: !!window.pwned}; }""")
    got = _browser_run(run, {"cockpit": page, "cockpit/data": data})
    if got is None:
        return
    assert got == {"options": [[label, label + " (replace)"]], "imgs": 0, "pwned": False}, \
        f"the Sheet's stint label is text, never markup: {got}"


def t_an_old_browser_without_media_query_listeners_still_polls():
    page = _composed(("cockpit", "cockpit.html"))

    def run(browser, serve):
        tab = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        tab.on("pageerror", lambda e: errors.append(str(e)))
        tab.add_init_script("MediaQueryList.prototype.addEventListener = undefined;")
        tab.route("http://ck.test/**", serve)
        tab.goto("http://ck.test/cockpit")
        tab.wait_for_timeout(1500)
        return {"errors": errors, "tally": tab.text_content("#tally"),
                "cue_in_strip": tab.evaluate("document.getElementById('cueCritical').parentElement.id")}
    got = _browser_run(run, {"cockpit": page, "cockpit/data": _cockpit_data(on_air=True)})
    if got is None:
        return
    assert got == {"errors": [], "tally": "YOU ARE ON AIR", "cue_in_strip": "liveStrip"}, \
        f"Safari before 14 places the cue with addListener and keeps polling: {got}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

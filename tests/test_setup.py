#!/usr/bin/env python3
"""Stdlib unit checks for the panel sheet-control additions.
Run: python3 tests/test_setup.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


# ---------- webhook response check (v2 action echo) ----------

def t_webhook_ok_plain():
    ok, err = m.check_webhook_response(b'{"ok": true}')
    assert ok and err is None


def t_webhook_ok_with_echo():
    ok, err = m.check_webhook_response(b'{"ok": true, "action": "setup", "v": 2}',
                                       expected_action="setup")
    assert ok and err is None


def t_webhook_v1_script_is_outdated_for_actions():
    # a v1 timer-only script answers ok WITHOUT the action echo -> not a success
    ok, err = m.check_webhook_response(b'{"ok": true}', expected_action="setup")
    assert not ok and "outdated" in err


def t_webhook_error_body():
    ok, err = m.check_webhook_response(b'{"error": "bad key"}')
    assert not ok and "bad key" in err


def t_webhook_garbage_body():
    ok, err = m.check_webhook_response(b"<html>Apps Script error page</html>")
    assert not ok and "did not confirm" in err
    ok, err = m.check_webhook_response(b"")
    assert not ok


# ---------- schedule rows (url + name) ----------

SCHED_CSV = ('"https://www.youtube.com/watch?v=abc",Matt\n'
             '"UCLA_DiR1FfKNvjuUpBHmylQ",NASA\n'
             '"UCoMdktPbSTixAyNGwb-UYkQ"\n')


def t_parse_rows_url_and_name():
    rows = m.ScheduleSource._parse_rows(SCHED_CSV)
    assert rows == [("https://www.youtube.com/watch?v=abc", "Matt", "", 1),
                    ("UCLA_DiR1FfKNvjuUpBHmylQ", "NASA", "", 2),
                    ("UCoMdktPbSTixAyNGwb-UYkQ", "", "", 3)], rows


def t_parse_rows_empty_is_none():
    assert m.ScheduleSource._parse_rows("url\n\n") is None


def t_parse_csv_still_returns_urls():
    items = m.ScheduleSource._parse_csv(SCHED_CSV)
    assert items[0] == "https://www.youtube.com/watch?v=abc"
    assert len(items) == 3


def t_schedule_source_get_rows():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    s = m.ScheduleSource("http://sched", _os.path.join(d, "cache.txt"), None)
    s.fetch = lambda timeout=15: m.ScheduleSource._parse_rows(SCHED_CSV)
    assert s.refresh() is True
    assert s.get() == ["https://www.youtube.com/watch?v=abc",
                       "UCLA_DiR1FfKNvjuUpBHmylQ", "UCoMdktPbSTixAyNGwb-UYkQ"]
    assert s.get_rows()[1] == ("UCLA_DiR1FfKNvjuUpBHmylQ", "NASA", "", 2)


def _sched_with_rows(rows):
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    s = m.ScheduleSource("http://sched", _os.path.join(d, "cache.txt"), None)
    s.fetch = lambda timeout=15, _r=rows: list(_r)
    s.refresh()
    return s


def t_inject_row_clears_url_keeps_name_stint():
    # A URL clear (url="") must update the in-memory row to url="" while KEEPING
    # streamer+stint, so /schedule/data + /cockpit/data reflect it before the next
    # sheet poll (bug: the cockpit "(replace)" tag lingered until a browser reload).
    s = _sched_with_rows([("https://www.youtube.com/watch?v=x", "JeGr", "Stint 1", 2)])
    assert s.inject_row(2, url="", name="JeGr", stint="Stint 1") is True
    assert s.get_rows() == [("", "JeGr", "Stint 1", 2)]


def t_inject_row_url_none_leaves_url_untouched():
    # url=None means "leave the URL alone"; only the name changes here.
    s = _sched_with_rows([("https://www.youtube.com/watch?v=x", "JeGr", "Stint 1", 2)])
    assert s.inject_row(2, name="GT45") is True
    assert s.get_rows() == [("https://www.youtube.com/watch?v=x", "GT45", "Stint 1", 2)]


def t_inject_row_rejects_junk_url():
    s = _sched_with_rows([("https://www.youtube.com/watch?v=x", "JeGr", "Stint 1", 2)])
    assert s.inject_row(2, url="not a url") is False
    assert s.get_rows() == [("https://www.youtube.com/watch?v=x", "JeGr", "Stint 1", 2)]


def t_inject_row_fully_empty_drops_row():
    # Clearing every cell drops the row, matching the parser (which skips blank
    # rows) so the in-memory schedule never diverges from a re-poll.
    s = _sched_with_rows([("https://www.youtube.com/watch?v=x", "JeGr", "Stint 1", 2),
                          ("https://www.youtube.com/watch?v=y", "GT45", "Stint 2", 3)])
    assert s.inject_row(2, url="", name="", stint="") is True
    assert s.get_rows() == [("https://www.youtube.com/watch?v=y", "GT45", "Stint 2", 3)]


def t_parse_rows_url_not_first_column():
    text = "Commentator,Channel\nMatt,UCaaaaaaaaaaaaaaaaaaaaaa\nNASA,UCbbbbbbbbbbbbbbbbbbbbbb\n"
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("UCaaaaaaaaaaaaaaaaaaaaaa", "", "", 2),
                    ("UCbbbbbbbbbbbbbbbbbbbbbb", "", "", 3)], rows


def t_parse_rows_with_header_line():
    # No 'URL' header -> positional fallback; the header row fails is_channel and
    # is skipped, physical line numbers preserved, no stint label.
    text = "Channel,Name\nUCaaaaaaaaaaaaaaaaaaaaa1,Alpha\nUCbbbbbbbbbbbbbbbbbbbbb2,Beta\n"
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("UCaaaaaaaaaaaaaaaaaaaaa1", "Alpha", "", 2),
                    ("UCbbbbbbbbbbbbbbbbbbbbb2", "Beta", "", 3)], rows


def t_parse_rows_header_mode_reads_stint():
    # A recognized 'URL' header opts into header mode: URL/Streamer/Stint located
    # by name (in any order), the per-stint label read, line numbers physical.
    text = ("Stint,URL,Streamer\n"
            "Opening,UCaaaaaaaaaaaaaaaaaaaaa1,JeGr\n"
            "Closing,UCbbbbbbbbbbbbbbbbbbbbb2,GT45\n")
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("UCaaaaaaaaaaaaaaaaaaaaa1", "JeGr", "Opening", 2),
                    ("UCbbbbbbbbbbbbbbbbbbbbb2", "GT45", "Closing", 3)], rows


def t_parse_rows_header_mode_missing_stint_column():
    # Header mode with only URL + Streamer -> stint label is "" (column absent).
    text = "URL,Streamer\nUCaaaaaaaaaaaaaaaaaaaaa1,JeGr\n"
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("UCaaaaaaaaaaaaaaaaaaaaa1", "JeGr", "", 2)], rows


def t_parse_rows_header_mode_keeps_planned_stints_without_url():
    # #137: in header mode a pre-planned stint (Stint label and/or Streamer set,
    # URL still blank) is a REAL stint slot — kept with an empty URL (feed idles
    # until filled) so the panel shows all planned stints, not just URL-bearing ones.
    text = ("Stint,URL,Streamer\n"
            "Opening,UCaaaaaaaaaaaaaaaaaaaaa1,JeGr\n"   # live: has URL
            "Mid,,GT45\n"                                # planned: stint + streamer, no URL
            "Closing,,\n"                                # planned: stint label only
            ",,\n")                                      # spacer: nothing -> dropped
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("UCaaaaaaaaaaaaaaaaaaaaa1", "JeGr", "Opening", 2),
                    ("", "GT45", "Mid", 3),
                    ("", "", "Closing", 4)], rows


def t_parse_rows_header_mode_planned_streamer_only():
    # A row with only a Streamer (no stint label, no URL) still counts as a stint.
    text = "URL,Streamer,Stint\n,JeGr,\n"
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("", "JeGr", "", 2)], rows


def t_parse_rows_reads_name_header_for_pov_tab():
    # The POV tab uses a 'name' column (no 'streamer'); header mode reads it into
    # the row's name field so the relay can surface the POV name.
    text = "url,name\nhttps://www.youtube.com/watch?v=p,JeGr\n"
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("https://www.youtube.com/watch?v=p", "JeGr", "", 2)], rows


def t_parse_rows_streamer_still_wins_over_name():
    # Additive change must not regress the Schedule tab: when both 'streamer' and
    # 'name' headers exist, 'streamer' is the one read (first match wins).
    text = ("url,streamer,name\n"
            "https://www.youtube.com/watch?v=p,RealStreamer,SomethingElse\n")
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("https://www.youtube.com/watch?v=p", "RealStreamer", "", 2)], rows


def t_parse_rows_header_mode_drops_invalid_url_keeps_planned():
    # A non-channel URL on an otherwise-planned row is treated as not-yet-filled
    # (url -> ""), so the feed never tries to serve junk but the row still shows.
    text = "Stint,URL,Streamer\nMid,not-a-url,GT45\n"
    rows = m.ScheduleSource._parse_rows(text)
    assert rows == [("", "GT45", "Mid", 2)], rows


def t_items_idle_on_planned_rows_without_url():
    # The feed URL list stays parallel to the rows: a planned (URL-less) stint is
    # an empty slot, so the feed idles on it instead of breaking the indexing.
    import tempfile, os as _os
    text = "Stint,URL,Streamer\nOpening,UCaaaaaaaaaaaaaaaaaaaaa1,JeGr\nMid,,GT45\n"
    s = m.ScheduleSource("http://sched",
                         _os.path.join(tempfile.mkdtemp(), "cache.txt"), None)
    s.fetch = lambda timeout=15: m.ScheduleSource._parse_rows(text)
    assert s.refresh() is True
    assert s.get() == ["UCaaaaaaaaaaaaaaaaaaaaa1", ""]   # planned stint = idle slot


# ---------- SetupControl ----------

OVERLAY_CSV = (",Stint,Intro,,,,,,,\n,Streamer,JeGr,,,,,,,\n"
               ",Session,Warmup,,,,,,,\n,Race Control,,,,,,,,\n")
CONFIG_CSV = ("Stints,Streamers,Session,Race Control,Flag,Teams,Brand Name\n"
              "Stint 1,JeGr,Qualifier,Formation Lap,Yellow Flag,T #1,Porsche\n"
              "Stint 2,GT45,Race,Final Lap,Safety Car,T #2,BMW\n")


def _hs_stub():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    hs._fetch = lambda url, timeout=10: OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    hs.refresh()
    return hs


def _ctl(pushes, response=b'{"ok": true, "action": "%s", "v": 2}', pov_source=None):
    hs = _hs_stub()
    ctl = m.SetupControl("http://push", hs, pov_source=pov_source)
    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        return response % payload["action"].encode() if b"%s" in response else response
    m.post_webhook, orig = fake_post, m.post_webhook
    return ctl, hs, orig


def t_set_field_unknown_field_and_value():
    ctl = m.SetupControl("http://push", _hs_stub())
    assert "error" in ctl.set_field("nope", "x")
    assert "error" in ctl.set_field("streamer", "Not In Vocab")
    assert "error" in ctl.set_field("streamer", "")   # racecontrol/flag may clear; others can't


def t_set_field_requires_webhook():
    r = m.SetupControl(None, _hs_stub()).set_field("streamer", "GT45")
    assert "error" in r and "webhook" in r["error"]


def t_set_field_sets_override_and_pushes():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        r = ctl.set_field("streamer", "GT45", now=1000.0)
        assert r.get("ok") and r.get("pending")
        assert hs.data(now=1001.0)["streamer"] == "GT45"   # echo immediate
        ctl._push_setup("Streamer", "GT45")                # the thread body, run sync
        assert pushes[-1] == {"action": "setup", "fields": {"Streamer": "GT45"}}
        assert ctl.push_status == "ok"
    finally:
        m.post_webhook = orig


def t_clear_racecontrol_allowed():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        r = ctl.set_field("racecontrol", "", now=1000.0)
        assert r.get("ok")
        ctl._push_setup("Race Control", "")
        assert pushes[-1]["fields"] == {"Race Control": ""}
    finally:
        m.post_webhook = orig


def t_clear_flag_allowed():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        r = ctl.set_field("flag", "", now=1000.0)
        assert r.get("ok")
        ctl._push_setup("Flag", "")
        assert pushes[-1]["fields"] == {"Flag": ""}
    finally:
        m.post_webhook = orig

def t_set_flag_validates_against_vocab():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        assert ctl.set_field("flag", "Safety Car", now=1000.0).get("ok")
        assert hs.data(now=1001.0)["flag"] == "Safety Car"      # optimistic echo
        assert "error" in ctl.set_field("flag", "Not A Flag")   # not in vocab
    finally:
        m.post_webhook = orig


def t_v1_script_reported_outdated():
    pushes = []
    ctl, hs, orig = _ctl(pushes, response=b'{"ok": true}')   # no action echo
    try:
        ctl._push_setup("Streamer", "GT45")
        assert ctl.push_status == "failed"
        assert "outdated" in ctl.last_error
    finally:
        m.post_webhook = orig


def t_schedule_set_validates_and_pushes():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        assert "error" in ctl.schedule_set("x", url="https://youtu.be/a")
        assert "error" in ctl.schedule_set(0, url="https://youtu.be/a")
        assert "error" in ctl.schedule_set(1, url="not a url")
        r = ctl.schedule_set(2, url="https://www.youtube.com/watch?v=x", name="JeGr")
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "schedule", "row": 2,
                              "url": "https://www.youtube.com/watch?v=x", "name": "JeGr"}
    finally:
        m.post_webhook = orig


def t_schedule_set_validates_streamer_and_stint_vocab():
    # Streamer + Stint are vocabulary-constrained, like the Setup fields:
    # an off-vocab value is rejected before any webhook call (no free text).
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        assert "error" in ctl.schedule_set(2, name="Nobody")          # not a streamer
        assert "error" in ctl.schedule_set(2, stint="Made Up Stint")   # not a stint
        assert pushes == []                                            # rejected pre-push
        r = ctl.schedule_set(2, url="https://www.youtube.com/watch?v=x",
                             name="GT45", stint="Stint 2")
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "schedule", "row": 2,
                              "url": "https://www.youtube.com/watch?v=x",
                              "name": "GT45", "stint": "Stint 2"}
    finally:
        m.post_webhook = orig


def t_schedule_set_clear_reflects_in_source():
    # End-to-end: a panel CLEAR URL (url="") writes the sheet AND updates the
    # in-memory schedule immediately (name/stint kept), so consumers don't show
    # the stale link for a poll interval.
    pushes = []
    hs = _hs_stub()
    s = _sched_with_rows([("https://www.youtube.com/watch?v=x", "JeGr", "Stint 1", 2)])
    ctl = m.SetupControl("http://push", hs, schedule_source=s)

    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        return b'{"ok": true, "action": "schedule", "v": 2}'
    m.post_webhook, orig = fake_post, m.post_webhook
    try:
        r = ctl.schedule_set(2, url="", name="JeGr", stint="Stint 1")
        assert r.get("ok"), r
        assert pushes[-1]["url"] == ""                       # cleared in the sheet
        assert s.get_rows() == [("", "JeGr", "Stint 1", 2)]  # and live in memory
    finally:
        m.post_webhook = orig


def t_schedule_set_accepts_local_but_pov_does_not():
    # #592: the director may point a stint at the capture card (`local:`); the POV
    # stays remote-only. The token is written normalised.
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        r = ctl.schedule_set(2, url=" Local: ")
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "schedule", "row": 2, "url": "local:"}
        assert "error" in ctl.pov_set("local:")
    finally:
        m.post_webhook = orig

def t_pov_set_pushes():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        assert "error" in ctl.pov_set("nonsense")
        r = ctl.pov_set("https://www.youtube.com/watch?v=p")
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "pov", "url": "https://www.youtube.com/watch?v=p"}
    finally:
        m.post_webhook = orig


class _RefreshSpy:
    """Minimal pov_source stub: records refresh() calls."""
    def __init__(self):
        self.refreshed = 0
    def refresh(self, timeout=15):     # match ScheduleSource.refresh's default
        self.refreshed += 1
        return True


def t_pov_set_with_name_pushes_clamped_and_refreshes():
    pushes = []
    spy = _RefreshSpy()
    ctl, hs, orig = _ctl(pushes, pov_source=spy)
    try:
        r = ctl.pov_set("https://www.youtube.com/watch?v=p", "A Very Long Driver Name Here")
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "pov",
                              "url": "https://www.youtube.com/watch?v=p",
                              "name": "A Very Long Driver N"}    # clamped to 20 chars
        assert len(pushes[-1]["name"]) == 20
        assert spy.refreshed == 1                                 # name applied immediately
    finally:
        m.post_webhook = orig


def t_pov_set_empty_name_clears():
    pushes = []
    spy = _RefreshSpy()
    ctl, hs, orig = _ctl(pushes, pov_source=spy)
    try:
        r = ctl.pov_set("https://www.youtube.com/watch?v=p", "")
        assert r.get("ok"), r
        assert pushes[-1]["name"] == ""                           # explicit clear
    finally:
        m.post_webhook = orig


def t_setup_data_shape():
    ctl = m.SetupControl(None, _hs_stub())
    d = ctl.data()
    # Scalar fields are still present and correct (team slots add p1/p2/p3 too).
    assert d["fields"]["stint"] == "Intro"
    assert d["fields"]["streamer"] == "JeGr"
    assert d["fields"]["session"] == "Warmup"
    assert d["fields"]["racecontrol"] == ""
    assert d["options"]["racecontrol"] == ["Formation Lap", "Final Lap"]
    assert d["fields"]["flag"] == ""
    assert d["options"]["flag"] == ["Yellow Flag", "Safety Car"]
    assert d["pending"] == [] and d["push"] == "disabled"


def t_schedule_set_rejects_bool_float_and_noop():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        assert "error" in ctl.schedule_set(True, url="https://youtu.be/a")
        assert "error" in ctl.schedule_set(2.9, url="https://youtu.be/a")
        assert "error" in ctl.schedule_set(1)          # nothing to write
        assert "error" in ctl.schedule_set(1, url=42)
        assert "error" in ctl.schedule_set(1, name=42)
        assert "error" in ctl.pov_set(42)
        assert pushes == []                            # no webhook call on any error
    finally:
        m.post_webhook = orig


def t_push_failure_keeps_override_until_ttl():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    _base, m.WEBHOOK_RETRY_BASE_S = m.WEBHOOK_RETRY_BASE_S, 0.0
    try:
        def boom(url, payload, timeout=10):
            raise OSError("down")
        m.post_webhook = boom
        ctl.set_field("streamer", "GT45", now=1000.0)
        ctl._push_setup("Streamer", "GT45")            # thread body, run sync
        assert ctl.push_status == "failed"
        assert hs.data(now=1005.0)["streamer"] == "GT45"   # override retained
        assert hs.data(now=1000.0 + m.OVERRIDE_TTL + 1)["streamer"] == "JeGr"  # sheet truth after TTL
    finally:
        m.post_webhook = orig
        m.WEBHOOK_RETRY_BASE_S = _base


# ---------- endpoint routing (real server, ephemeral port) ----------

def _client(setup_ctl, next_result=None, rows=None, live_idx=0, qual_rows=None):
    import json as _json, threading as _t, urllib.error
    from urllib.request import urlopen, Request

    class _StubFeed:
        def __init__(self, idx): self.idx = idx

    class _StubSource:
        def __init__(self, rows): self._rows = rows
        def get_rows(self): return list(self._rows)
        def health(self): return {"count": len(self._rows),
                                  "last_ok_age_s": 0.0, "last_error": None}

    class _StubRelay:
        def __init__(self):
            # Simulate a header at line 1: physical sheet rows are 2 and 3.
            self.race_source = _StubSource(rows if rows is not None else
                                           [("https://www.youtube.com/watch?v=a", "Alpha", "", 2),
                                            ("UCLA_DiR1FfKNvjuUpBHmylQ", "Beta", "", 3)])
            self.qual_source = _StubSource(qual_rows) if qual_rows is not None else None
            self.mode = "race"
            self.feeds = {"A": _StubFeed(0), "B": _StubFeed(1)}
        @property
        def source(self):
            return (self.qual_source if (self.mode == "qualifying" and self.qual_source)
                    else self.race_source)
        def status(self): return {"schedule_len": 2, "mode": self.mode, "feeds": {}}
        def next_auto(self): return dict(next_result or {"obs_cut": False})
        def set_stint(self, n): return self.status()
        def set_mode(self, mode):
            if mode not in ("race", "qualifying"):
                return {"error": f"unknown mode: {mode!r}"}
            if mode == "qualifying" and not self.qual_source:
                return {"error": "qualifying disabled"}
            self.mode = mode
            return self.status()
        def live_schedule_row(self):
            return m.live_schedule_row(self.source.get_rows(), live_idx)

    handler = m.make_handler(_StubRelay(), setup_ctl=setup_ctl)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def _read(req):
        # error endpoints answer 404 etc. with a JSON body -> read it either way
        try:
            with urlopen(req, timeout=5) as r:
                return _json.loads(r.read())
        except urllib.error.HTTPError as e:
            return _json.loads(e.read())

    def get(path):
        return _read(base + path)

    def post(path, body):
        return _read(Request(base + path, data=_json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"},
                             method="POST"))

    return srv, get, post


def t_endpoints_setup_data_and_set():
    ctl = m.SetupControl(None, _hs_stub())   # push disabled -> set errors cleanly
    srv, get, post = _client(ctl)
    try:
        d = get("/setup/data")
        assert d["fields"]["streamer"] == "JeGr" and d["push"] == "disabled"
        r = get("/setup/set/streamer/GT45")
        assert "webhook not configured" in r["error"]
        r = get("/setup/clear/racecontrol")
        assert "webhook not configured" in r["error"]
        assert "error" in get("/setup/bogus")
    finally:
        srv.shutdown()


def t_endpoints_setup_set_urlencoded_value():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    srv, get, post = _client(ctl)
    try:
        r = get("/setup/set/racecontrol/Formation%20Lap")
        assert r.get("ok") and r["value"] == "Formation Lap", r
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_next_handover_clears_racecontrol_on_cut():
    # One-button handover: /next cuts OBS back to Stint, so no STINT macro press
    # follows -> the relay must clear Race Control itself (mirrors rc:"").
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    srv, get, post = _client(ctl, next_result={"obs_cut": True})
    try:
        assert get("/setup/set/racecontrol/Formation%20Lap").get("ok")
        assert hs.data()["raceControl"] == "Formation Lap"   # set before the handover
        r = get("/next")
        assert r.get("obs_cut") is True
        assert hs.data()["raceControl"] == ""                # handover cleared it
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_next_handover_keeps_racecontrol_without_cut():
    # No real cut (incoming feed not yet serving) -> leave Race Control untouched.
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    srv, get, post = _client(ctl, next_result={"obs_cut": False})
    try:
        assert get("/setup/set/racecontrol/Formation%20Lap").get("ok")
        r = get("/next")
        assert r.get("obs_cut") is False
        assert hs.data()["raceControl"] == "Formation Lap"   # untouched
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_next_handover_keeps_flag_on_cut():
    # A track condition (flag) outlives a commentator handover -> NOT cleared on cut.
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    srv, get, post = _client(ctl, next_result={"obs_cut": True})
    try:
        assert get("/setup/set/flag/Safety%20Car").get("ok")
        assert hs.data()["flag"] == "Safety Car"
        get("/next")
        assert hs.data()["flag"] == "Safety Car"   # still set after the cut
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_next_handover_writes_schedule_streamer_and_stint_on_cut():
    # On a real cut the HUD follows the on-air stint's Streamer + Stint label
    # from the Schedule (issue #112), via the async-optimistic set_field path.
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    live = [("https://www.youtube.com/watch?v=a", "GT45", "Stint 2", 2)]
    srv, get, post = _client(ctl, next_result={"obs_cut": True}, rows=live, live_idx=0)
    try:
        r = get("/next")
        assert r.get("obs_cut") is True
        assert hs.data()["streamer"] == "GT45"     # optimistic echo from the schedule row
        assert hs.data()["stint"] == "Stint 2"
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_next_handover_skips_off_vocab_schedule_values():
    # A schedule streamer/stint outside the Configuration vocab is rejected by
    # set_field and silently skipped — the HUD keeps its prior value, no crash.
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    assert hs.data()["streamer"] == "JeGr"          # baseline from OVERLAY_CSV
    live = [("https://www.youtube.com/watch?v=a", "Nobody", "Made Up", 2)]
    srv, get, post = _client(ctl, next_result={"obs_cut": True}, rows=live, live_idx=0)
    try:
        assert get("/next").get("obs_cut") is True
        assert hs.data()["streamer"] == "JeGr"      # unchanged: off-vocab skipped
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_next_handover_no_write_without_cut():
    # No real cut -> no schedule-driven HUD write (the new feed isn't on air yet).
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    live = [("https://www.youtube.com/watch?v=a", "GT45", "Stint 2", 2)]
    srv, get, post = _client(ctl, next_result={"obs_cut": False}, rows=live, live_idx=0)
    try:
        assert get("/next").get("obs_cut") is False
        assert hs.data()["streamer"] == "JeGr"      # untouched
        assert hs.data()["stint"] == "Intro"
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_next_handover_writes_schedule_on_continuation_without_cut():
    # A same-URL back-to-back continuation does NOT cut OBS, but the DISPLAY
    # stint still advances -> the HUD label must follow it (review fix: gate on
    # should_push_live_schedule, not obs_cut alone).
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    live = [("https://www.youtube.com/watch?v=a", "GT45", "Stint 2", 2)]
    srv, get, post = _client(ctl, next_result={"continuation": True, "obs_cut": False},
                             rows=live, live_idx=0)
    try:
        r = get("/next")
        assert r.get("continuation") is True and r.get("obs_cut") is False
        assert hs.data()["streamer"] == "GT45"     # optimistic echo from the schedule row
        assert hs.data()["stint"] == "Stint 2"
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_set_stint_writes_schedule_streamer_and_stint():
    # Producer takeover (/set/stint) puts a fresh stint on air -> same auto-write
    # as /next, unconditionally (the director picks the scene).
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    live = [("https://www.youtube.com/watch?v=a", "GT45", "Stint 2", 2)]
    srv, get, post = _client(ctl, rows=live, live_idx=0)
    try:
        get("/set/stint/1")
        assert hs.data()["streamer"] == "GT45"
        assert hs.data()["stint"] == "Stint 2"
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_endpoints_qualifying_data():
    ctl = m.SetupControl(None, _hs_stub())
    qrows = [("https://www.youtube.com/watch?v=q", "GT45", "Stint 2", 2)]
    srv, get, post = _client(ctl, qual_rows=qrows)
    try:
        d = get("/qualifying/data")
        assert d["available"] is True and d["mode"] == "race"
        assert d["rows"][0] == {"row": 1, "sheetRow": 2,
                                "url": "https://www.youtube.com/watch?v=q",
                                "name": "GT45", "stint": "Stint 2", "live": None}
    finally:
        srv.shutdown()


def t_endpoints_qualifying_data_unavailable():
    ctl = m.SetupControl(None, _hs_stub())
    srv, get, post = _client(ctl)                 # no qual_rows -> qual_source None
    try:
        d = get("/qualifying/data")
        assert d["available"] is False and d["rows"] == []
    finally:
        srv.shutdown()


def t_endpoint_mode_switch_writes_qualifying_hud():
    # GET /mode/qualifying switches the active schedule and auto-fills the HUD
    # Streamer/Stint from the now-on-air qualifying row (issue #112 path).
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    qrows = [("https://www.youtube.com/watch?v=q", "GT45", "Stint 2", 2)]
    srv, get, post = _client(ctl, qual_rows=qrows, live_idx=0)
    try:
        r = get("/mode/qualifying")
        assert r.get("mode") == "qualifying"
        assert hs.data()["streamer"] == "GT45"
        assert hs.data()["stint"] == "Stint 2"
        assert get("/mode/race").get("mode") == "race"
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_endpoint_mode_qualifying_unavailable():
    ctl = m.SetupControl(None, _hs_stub())
    srv, get, post = _client(ctl)                 # no qual_source
    try:
        assert "error" in get("/mode/qualifying")
        assert "error" in get("/mode/bogus")
    finally:
        srv.shutdown()


def t_endpoints_qualifying_set_post():
    pushes = []
    ctl, qsrc, ssrc, orig = _qctl(pushes)
    srv, get, post = _client(ctl, qual_rows=[])
    try:
        r = post("/qualifying/set", {"row": 2, "url": "https://youtu.be/q",
                                     "name": "JeGr", "stint": "Stint 1"})
        assert r.get("ok"), r
        assert pushes[-1]["tab"] == "Qualifying"
        assert pushes[-1]["stint"] == "Stint 1"
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_endpoints_schedule_data_marks_live():
    ctl = m.SetupControl(None, _hs_stub())
    srv, get, post = _client(ctl)
    try:
        d = get("/schedule/data")
        assert d["rows"][0] == {"row": 1, "sheetRow": 2,
                                "url": "https://www.youtube.com/watch?v=a",
                                "name": "Alpha", "stint": "", "live": "A"}
        assert d["rows"][1]["live"] == "B"
        assert d["rows"][1]["sheetRow"] == 3
    finally:
        srv.shutdown()


def t_endpoints_post_writes():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    srv, get, post = _client(ctl)
    try:
        r = post("/schedule/set", {"row": 1, "url": "https://youtu.be/x",
                                   "name": "JeGr", "stint": "Stint 1"})
        assert r.get("ok"), r
        assert pushes[-1]["action"] == "schedule"
        assert pushes[-1]["stint"] == "Stint 1"
        r = post("/pov/set", {"url": "https://youtu.be/p"})
        assert r.get("ok"), r
        assert pushes[-1]["action"] == "pov"
        assert "error" in post("/pov/bogus", {})
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_endpoints_post_rejects_bad_json():
    import urllib.error
    from urllib.request import urlopen, Request
    ctl = m.SetupControl(None, _hs_stub())
    srv, get, post = _client(ctl)
    try:
        req = Request(f"http://127.0.0.1:{srv.server_address[1]}/pov/set",
                      data=b"not json", method="POST")
        try:
            urlopen(req, timeout=5)
            raise AssertionError("expected HTTP 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()


def t_inject_row_adds_link_before_poll():
    s = m.ScheduleSource(csv_url=None, cache_path=os.path.join(HERE, "_x.cache"),
                         local_fallback=None)
    s.items = ["s1"]; s.rows = [("s1", "Ann", "", 1)]
    assert s.inject_row(2, "https://www.youtube.com/watch?v=abc", "Ben", "Stint 2") is True
    assert s.get() == ["s1", "https://www.youtube.com/watch?v=abc"]
    assert s.get_rows()[1] == ("https://www.youtube.com/watch?v=abc", "Ben", "Stint 2", 2)


def t_inject_row_replaces_same_physical_row():
    s = m.ScheduleSource(csv_url=None, cache_path=os.path.join(HERE, "_x.cache"),
                         local_fallback=None)
    s.items = ["s1", "old"]; s.rows = [("s1", "Ann", "", 1), ("old", "X", "", 2)]
    s.inject_row(2, "UC1234567890123456789012", "New")
    assert s.get() == ["s1", "UC1234567890123456789012"]


def t_inject_row_empty_url_with_name_is_a_clear():
    # An empty URL is a valid CLEAR when a name/stint is present (the slot
    # survives as a planned stint with url=""); only a non-empty NON-channel URL
    # is rejected as junk.
    s = m.ScheduleSource(csv_url=None, cache_path=os.path.join(HERE, "_x.cache"),
                         local_fallback=None)
    s.items = ["s1"]; s.rows = [("s1", "Ann", "", 1)]
    assert s.inject_row(2, "", "Ben") is True
    assert s.get_rows()[-1] == ("", "Ben", "", 2)          # planned stint, url cleared
    assert s.inject_row(1, "not-a-channel", "Ann") is False  # junk url rejected
    assert s.get()[0] == "s1"                                # row 1 left untouched


def t_schedule_set_injects_on_success():
    src = m.ScheduleSource(csv_url=None, cache_path=os.path.join(HERE, "_y.cache"),
                           local_fallback=None)
    src.items = ["s1"]; src.rows = [("s1", "Ann", "", 1)]
    ctl = m.SetupControl(push_url="https://example.test/push", hud_source=None,
                         schedule_source=src)
    ctl._push = lambda payload, expected: (True, "")     # stub the webhook
    out = ctl.schedule_set(2, "https://www.youtube.com/watch?v=abc", "Ben")
    assert out.get("ok") is True
    assert src.get() == ["s1", "https://www.youtube.com/watch?v=abc"]   # available immediately


def t_schedule_set_no_inject_on_push_failure():
    src = m.ScheduleSource(csv_url=None, cache_path=os.path.join(HERE, "_z.cache"),
                           local_fallback=None)
    src.items = ["s1"]; src.rows = [("s1", "Ann", "", 1)]
    ctl = m.SetupControl(push_url="https://example.test/push", hud_source=None,
                         schedule_source=src)
    ctl._push = lambda payload, expected: (False, "boom")
    out = ctl.schedule_set(2, "https://www.youtube.com/watch?v=abc", "Ben")
    assert "error" in out
    assert src.get() == ["s1"]                            # nothing injected on failure


# ---------- qualifying (issue #124): separate tab, own source, Feed A ----------

def _qctl(pushes):
    """A SetupControl wired with BOTH a race schedule source and a qualifying
    source, plus the vocab HUD stub."""
    hs = _hs_stub()
    qsrc = m.ScheduleSource(csv_url=None, cache_path=os.path.join(HERE, "_q.cache"),
                            local_fallback=None)
    qsrc.items = []; qsrc.rows = []
    ssrc = m.ScheduleSource(csv_url=None, cache_path=os.path.join(HERE, "_s.cache"),
                            local_fallback=None)
    ssrc.items = []; ssrc.rows = []
    ctl = m.SetupControl("http://push", hs, schedule_source=ssrc, qual_source=qsrc)
    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        return b'{"ok": true, "action": "schedule", "v": 4}'
    m.post_webhook, orig = fake_post, m.post_webhook
    return ctl, qsrc, ssrc, orig


def t_qualifying_set_targets_qualifying_tab_and_injects_qual_source():
    pushes = []
    ctl, qsrc, ssrc, orig = _qctl(pushes)
    try:
        r = ctl.qualifying_set(2, url="https://www.youtube.com/watch?v=q",
                               name="GT45", stint="Stint 2")
        assert r.get("ok"), r
        # webhook payload carries the Qualifying tab target + the schedule action
        assert pushes[-1] == {"action": "schedule", "row": 2, "tab": "Qualifying",
                              "url": "https://www.youtube.com/watch?v=q",
                              "name": "GT45", "stint": "Stint 2"}
        # optimistic echo lands in the qualifying source, NOT the race schedule
        assert qsrc.get() == ["https://www.youtube.com/watch?v=q"]
        assert ssrc.get() == []
    finally:
        m.post_webhook = orig


def t_qualifying_set_validates_vocab():
    pushes = []
    ctl, qsrc, ssrc, orig = _qctl(pushes)
    try:
        assert "error" in ctl.qualifying_set(2, name="Nobody")          # off-vocab streamer
        assert "error" in ctl.qualifying_set(2, stint="Made Up")         # off-vocab stint
        assert pushes == []
    finally:
        m.post_webhook = orig


def t_schedule_set_has_no_tab_key():
    # The race schedule_set must NOT carry a tab (writes the default Schedule tab).
    pushes = []
    ctl, qsrc, ssrc, orig = _qctl(pushes)
    try:
        ctl.schedule_set(2, url="https://www.youtube.com/watch?v=x", name="JeGr")
        assert "tab" not in pushes[-1]
    finally:
        m.post_webhook = orig


TEAM_OVERLAY_CSV = (",Teams P1,Old A,,\n,Teams P2,Old B,,\n,Teams P3,,,\n")
TEAM_CONFIG_CSV = ("Teams,Number,Brand Name\n"
                   "OVO eSports,111,Porsche\nFeel Good,303,BMW\n")

def _team_ctl(pushes):
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config", _os.path.join(d, "h.json"))
    hs._fetch = lambda url, timeout=10: TEAM_OVERLAY_CSV if url == "http://overlay" else TEAM_CONFIG_CSV
    hs.refresh()
    ctl = m.SetupControl("http://push", hs)
    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        return b'{"ok": true, "action": "teams", "v": 2}'
    m.post_webhook, orig = fake_post, m.post_webhook
    return ctl, hs, orig

def t_set_team_validates_vocab():
    ctl, hs, orig = _team_ctl([])
    try:
        assert "error" in ctl.set_team("p1", "Not A Team")   # not in roster
        assert "error" in ctl.set_team("p9", "OVO eSports")   # bad slot
    finally:
        m.post_webhook = orig

def t_set_team_echo_and_push():
    pushes = []
    ctl, hs, orig = _team_ctl(pushes)
    try:
        r = ctl.set_team("p1", "OVO eSports", now=1000.0)
        assert r.get("ok") and r.get("pending")
        assert hs.data(now=1001.0)["teams"][0]["name"] == "OVO eSports"
        assert hs.data(now=1001.0)["teams"][0]["number"] == "111"
        ctl._push_team(1, "OVO eSports")                      # thread body, run sync
        assert pushes[-1] == {"action": "teams", "slot": 1, "name": "OVO eSports"}
        assert ctl.push_status == "ok"
    finally:
        m.post_webhook = orig

TEAM_CONFIG_CSV_EMBEDDED = ("Teams,Brand Name\n"
                            "OVO eSports #111,Porsche\nFeel Good #303,BMW\n")

def _team_ctl_embedded(pushes):
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config", _os.path.join(d, "h.json"))
    hs._fetch = lambda url, timeout=10: (TEAM_OVERLAY_CSV if url == "http://overlay"
                                         else TEAM_CONFIG_CSV_EMBEDDED)
    hs.refresh()
    ctl = m.SetupControl("http://push", hs)
    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        return b'{"ok": true, "action": "teams", "v": 2}'
    m.post_webhook, orig = fake_post, m.post_webhook
    return ctl, hs, orig

def t_push_team_sends_verbatim_label_with_number():
    # Panel offers the stripped name; the relay writes the verbatim '#NNN' label
    # the Setup dropdown lists.
    pushes = []
    ctl, hs, orig = _team_ctl_embedded(pushes)
    try:
        assert hs.full_team_name("OVO eSports") == "OVO eSports #111"
        ctl._push_team(1, "OVO eSports")
        assert pushes[-1] == {"action": "teams", "slot": 1, "name": "OVO eSports #111"}
    finally:
        m.post_webhook = orig

def t_full_team_name_falls_back_for_unknown_team():
    ctl, hs, orig = _team_ctl_embedded([])
    try:
        # unknown team -> the given name, stripped; never a KeyError
        assert hs.full_team_name("Mystery Crew") == "Mystery Crew"
    finally:
        m.post_webhook = orig

TEAM_CONFIG_CSV_DUP = ("Teams,Brand Name\n"
                       "Track Design Racing #2,Ferrari\n"
                       "Track Design Racing #4,AMG\n")

def _team_ctl_dup(pushes, overlay):
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config", _os.path.join(d, "h.json"))
    hs._fetch = lambda url, timeout=10: (overlay if url == "http://overlay"
                                         else TEAM_CONFIG_CSV_DUP)
    hs.refresh()
    ctl = m.SetupControl("http://push", hs)
    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        return b'{"ok": true, "action": "teams", "v": 2}'
    m.post_webhook, orig = fake_post, m.post_webhook
    return ctl, hs, orig

def t_setup_data_keeps_duplicate_team_numbers_in_dropdown():
    # Same name, different number: the dropdown must keep BOTH verbatim options
    # (regression — they collapsed to one, losing the #4 car), and the current
    # slot value must be the verbatim label so the <select> selects it.
    pushes = []
    ctl, hs, orig = _team_ctl_dup(
        pushes, ",Teams P1,Track Design Racing #4,,\n,Teams P2,,,\n,Teams P3,,,\n")
    try:
        d = ctl.data()
        assert d["options"]["p1"] == ["Track Design Racing #2",
                                      "Track Design Racing #4"], d["options"]["p1"]
        assert d["fields"]["p1"] == "Track Design Racing #4", d["fields"]["p1"]
    finally:
        m.post_webhook = orig

def t_set_team_distinct_cars_write_distinct_verbatim_labels():
    # Both same-name cars are assignable; each writes its OWN verbatim '#NNN'.
    pushes = []
    ctl, hs, orig = _team_ctl_dup(
        pushes, ",Teams P1,,,\n,Teams P2,,,\n,Teams P3,,,\n")
    try:
        assert ctl.set_team("p1", "Track Design Racing #2", now=1000.0).get("ok")
        assert ctl.set_team("p2", "Track Design Racing #4", now=1000.0).get("ok")
        assert hs.data(now=1001.0)["teams"][0]["number"] == "2"
        assert hs.data(now=1001.0)["teams"][1]["number"] == "4"
        ctl._push_team(1, "Track Design Racing #2")
        ctl._push_team(2, "Track Design Racing #4")
        assert pushes[-2] == {"action": "teams", "slot": 1, "name": "Track Design Racing #2"}
        assert pushes[-1] == {"action": "teams", "slot": 2, "name": "Track Design Racing #4"}
    finally:
        m.post_webhook = orig

def t_setup_data_includes_teams():
    ctl, hs, orig = _team_ctl([])
    try:
        d = ctl.data()
        assert d["options"]["p1"] == ["OVO eSports", "Feel Good"]
        assert d["fields"]["p1"] == "Old A" and d["fields"]["p2"] == "Old B"
        assert "p1" in d["options"] and "p2" in d["options"] and "p3" in d["options"]
    finally:
        m.post_webhook = orig


def t_endpoint_setup_team_sets_slot():
    ctl, hs, orig = _team_ctl([])
    srv, get, post = _client(ctl)
    try:
        r = get("/setup/team/p1/OVO%20eSports")
        assert r.get("ok") and r.get("slot") == "p1" and r.get("value") == "OVO eSports", r
        d = get("/setup/data")                       # optimistic echo visible in slot p1
        assert d["fields"]["p1"] == "OVO eSports", d
    finally:
        srv.shutdown(); m.post_webhook = orig


def t_set_teams_override_atomic_present():
    # All given slot overrides are visible after a single batch call.
    _ctl_unused, hs, orig = _team_ctl([])
    try:
        hs.set_teams_override({0: hs.resolve_team("OVO eSports"),
                               2: hs.resolve_team("Feel Good")}, now=1000.0)
        assert hs.team_pending(now=1001.0) == {0, 2}
        d = hs.data(now=1001.0)
        assert d["teams"][0]["name"] == "OVO eSports"
        assert d["teams"][2]["name"] == "Feel Good"
    finally:
        m.post_webhook = orig


def t_set_teams_validates_all_or_nothing():
    pushes = []
    ctl, hs, orig = _team_ctl(pushes)
    try:
        # one bad value in the batch -> nothing applied, nothing written
        r = ctl.set_teams({"p1": "OVO eSports", "p2": "Not A Team"}, now=1000.0)
        assert "error" in r
        assert hs.team_pending(now=1001.0) == set()
        assert pushes == []
        assert "error" in ctl.set_teams({"p9": "OVO eSports"})   # bad slot key
        assert "error" in ctl.set_teams(None)                    # not a dict
    finally:
        m.post_webhook = orig


def t_set_teams_atomic_echo_and_pushes():
    pushes = []
    ctl, hs, orig = _team_ctl(pushes)
    try:
        r = ctl.set_teams({"p1": "OVO eSports", "p2": "Feel Good",
                           "p3": "OVO eSports"}, now=1000.0)
        assert r.get("ok") and r.get("pending"), r
        assert hs.team_pending(now=1001.0) == {0, 1, 2}          # all three atomic
        d = hs.data(now=1001.0)
        assert [t["name"] for t in d["teams"][:3]] == \
            ["OVO eSports", "Feel Good", "OVO eSports"]
        ctl._push_teams([(1, "OVO eSports"), (2, "Feel Good"),
                         (3, "OVO eSports")])                    # thread body, run sync
        assert {"action": "teams", "slot": 1, "name": "OVO eSports"} in pushes
        assert {"action": "teams", "slot": 2, "name": "Feel Good"} in pushes
        assert {"action": "teams", "slot": 3, "name": "OVO eSports"} in pushes
        assert ctl.push_status == "ok"
    finally:
        m.post_webhook = orig


def t_push_teams_partial_failure_marks_failed():
    # A later slot's success must not mask an earlier slot's failed Sheet write
    # (without the explicit override push_status would read "ok" and the panel
    # would show "sheet sync OK" while slot 1 silently reverts after the TTL).
    pushes = []
    ctl, hs, orig = _team_ctl(pushes)
    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        if payload.get("slot") == 1:                 # slot 1 fails, 2 & 3 succeed
            return b'{"ok": false, "error": "boom"}'
        return b'{"ok": true, "action": "teams", "v": 2}'
    m.post_webhook = fake_post
    try:
        ctl._push_teams([(1, "OVO eSports"), (2, "Feel Good"), (3, "OVO eSports")])
        assert ctl.push_status == "failed", ctl.push_status
        assert ctl.last_error                          # carries the first failure
    finally:
        m.post_webhook = orig


def t_endpoints_setup_teams_post():
    pushes = []
    ctl, hs, orig = _team_ctl(pushes)
    srv, get, post = _client(ctl)
    try:
        r = post("/setup/teams", {"teams": {"p1": "OVO eSports",
                                            "p2": "Feel Good", "p3": "OVO eSports"}})
        assert r.get("ok") and r.get("pending"), r
        assert hs.team_pending() == {0, 1, 2}
        assert "error" in post("/setup/teams", {"teams": {"p1": "Not A Team"}})
    finally:
        srv.shutdown(); m.post_webhook = orig


# ---------- is_channel host allow-list + argv separators (SSRF/arg-injection #4) ----

def t_is_channel_accepts_youtube_and_twitch():
    for good in ("https://www.youtube.com/watch?v=abc",
                 "https://youtu.be/abc",
                 "http://youtube.com/watch?v=abc",
                 "https://m.youtube.com/watch?v=abc",
                 "https://www.twitch.tv/somechannel",
                 "https://twitch.tv/somechannel",
                 "UCabcdefghijklmnopqrstuv"):
        assert m.is_channel(good), good


def t_is_channel_rejects_ssrf_and_flags():
    for bad in ("http://169.254.169.254/latest/meta-data/",
                "http://localhost:8088/status",
                "http://127.0.0.1/x",
                "http://192.168.1.10/x",
                "file:///etc/passwd",
                "https://youtube.com.evil.com/x",      # suffix trick
                "https://youtube.com@evil.com/x",       # userinfo trick -> host is evil.com
                "--config-location=/tmp/x",             # yt-dlp flag injection
                "ftp://youtube.com/x",
                "", "   "):
        assert not m.is_channel(bad), bad


def t_ytdlp_resolve_cmd_separates_url():
    cmd = m.ytdlp_resolve_cmd("https://youtu.be/AAA", None)
    assert cmd[-2:] == ["--", "https://youtu.be/AAA"], cmd
    assert "--cookies" not in cmd
    cmd2 = m.ytdlp_resolve_cmd("https://youtu.be/AAA", "/c/cookies.txt")
    assert cmd2[-2:] == ["--", "https://youtu.be/AAA"], cmd2
    assert cmd2.index("--cookies") < cmd2.index("--")          # cookies stay an option
    assert cmd2[cmd2.index("--cookies") + 1] == "/c/cookies.txt"


def t_ytdlp_resolve_cmd_never_ignores_no_formats():
    # #621: --ignore-no-formats-error would turn the bot-check / rate-limit reason into a
    # hidden warning, so it belongs only on the separate live-status call.
    assert "--ignore-no-formats-error" not in m.ytdlp_resolve_cmd("https://youtu.be/AAA", "/c/j.txt")


def t_ytdlp_resolve_cmd_asks_for_a_muxed_capable_player_client():
    # 2026-09-21, found on a live GT7 stream that racecast refused with "Requested format
    # is not available": yt-dlp's default player client returned ONLY video-only and
    # audio-only HLS renditions for it, while `b[height<=1080]/b` asks for a MUXED format.
    # No muxed format existed, so a perfectly normal YouTube live stream could not be
    # pulled at all. Listing a second client makes yt-dlp union both clients' formats, so
    # the default path still wins whenever it already offers a muxed one.
    cmd = m.ytdlp_resolve_cmd("https://youtu.be/AAA", None)
    i = cmd.index("--extractor-args")
    arg = cmd[i + 1]
    assert arg.startswith("youtube:player_client="), arg
    clients = arg.split("=", 1)[1].split(",")
    assert clients[0] == "default", f"the default client must stay first, got {clients}"
    assert len(clients) > 1, "a fallback client is the whole point"
    assert i < cmd.index("--"), "it is an option, not part of the URL"


def t_ytdlp_live_status_cmd():
    cmd = m.ytdlp_live_status_cmd("https://youtu.be/AAA", "/c/j.txt")
    assert cmd[-2:] == ["--", "https://youtu.be/AAA"], cmd
    head = cmd[:cmd.index("--")]
    assert "--ignore-no-formats-error" in head and "--skip-download" in head, cmd
    assert head[head.index("--print") + 1] == "rcs %(live_status)s", cmd
    assert head[head.index("--cookies") + 1] == "/c/j.txt", cmd
    assert "--cookies" not in m.ytdlp_live_status_cmd("https://youtu.be/AAA", None)


def t_streamlink_serve_cmd_separates_url():
    cmd = m.streamlink_serve_cmd("http://hls.example/x.m3u8", 53001)
    assert cmd[-3:] == ["--", "http://hls.example/x.m3u8", "best"], cmd
    assert "--player-external-http-port" in cmd
    sep = cmd.index("--")
    assert all(not str(x).startswith("http") for x in cmd[:sep])   # options precede the URL


def _panel_details_classes(html, box_id):
    import re
    tag = re.search(rf'<details[^>]*\bid="{box_id}"[^>]*>', html)
    assert tag, f"panel section {box_id} not found"
    cls = re.search(r'\bclass="([^"]*)"', tag.group(0))
    return cls.group(1).split() if cls else []


def t_panel_qualifying_section_is_styled():
    """#134 (superseded by the race/qualifying schedule-mode merge): the Qualifying
    region is no longer a standalone <details id="qualBox"> — it is the `#qualSched`
    div nested inside the single merged `#urlsBox` (class="bus urls"), so it inherits
    the schedule section's table/input/select/button styling automatically. Guards
    against a regression back to an unstyled standalone qualifying block."""
    with open(os.path.join(ROOT, "src", "director", "director-panel.html"),
              encoding="utf-8") as fh:
        html = fh.read()
    assert "urls" in _panel_details_classes(html, "urlsBox")        # schedule (reference)
    urls_start = html.index('id="urlsBox"')
    urls_end = html.index("</details>", urls_start)
    qual_pos = html.index('id="qualSched"')
    assert urls_start < qual_pos < urls_end, \
        "qualifying region must be nested inside the merged, styled #urlsBox"


def t_panel_schedule_qualifying_selects_are_styled():
    """#152: the Streamer/Stint <select> dropdowns in the Schedule and Qualifying
    tables must carry the same dark dropdown styling as the HUD section's `.fld
    select`. Without a dedicated rule the `.nm` selects fall back to bare browser
    defaults and the `.st` selects even inherit the unrelated status-pill (`.st`)
    look. Guard a `.urls select` rule that matches the HUD dropdown style."""
    import re
    with open(os.path.join(ROOT, "src", "director", "director-panel.html"),
              encoding="utf-8") as fh:
        html = fh.read()
    style = re.search(r"<style>(.*?)</style>", html, re.S)
    assert style, "panel <style> block not found"
    css = style.group(1)
    rule = re.search(r"\.urls\s+select\s*\{([^}]*)\}", css)
    assert rule, ".urls select rule missing -> schedule/qualifying dropdowns unstyled (#152)"
    body = rule.group(1)
    # mirror the HUD dropdown look (.fld select): dark fill + edge border + mono font
    assert "background" in body and "border" in body and "font-family" in body, \
        ".urls select rule must set background/border/font-family like .fld select (#152)"
    # focus affordance, matching the HUD dropdowns
    assert re.search(r"\.urls\s+select:focus\s*\{", css), \
        ".urls select:focus rule missing -> no focus affordance like the HUD dropdowns (#152)"


def t_crew_set_validates_and_pushes():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        assert "error" in ctl.crew_set("x", name="Dana")        # non-numeric row
        assert "error" in ctl.crew_set(0, name="Dana")          # row < 1
        assert "error" in ctl.crew_set(2, name="")              # empty name
        assert pushes == []                                     # all rejected pre-push
        r = ctl.crew_set(2, name="Dana", director=True, producer=False)
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "crew", "row": 2, "name": "Dana",
                              "director": True, "producer": False,
                              "commentator": False, "race_control": False,
                              "discord": ""}
    finally:
        m.post_webhook = orig


def t_crew_set_coerces_flags_and_strips_name():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        r = ctl.crew_set(3, name="  Pia  ", director=0, producer="x",
                         commentator="yes", race_control="x", discord="  Pia#1  ")
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "crew", "row": 3, "name": "Pia",
                              "director": False, "producer": True,
                              "commentator": True, "race_control": True,
                              "discord": "Pia#1"}
    finally:
        m.post_webhook = orig


def t_crew_delete_pushes_delete_flag():
    pushes = []
    ctl, hs, orig = _ctl(pushes)
    try:
        assert "error" in ctl.crew_delete(0)
        r = ctl.crew_delete(2)
        assert r.get("ok"), r
        assert pushes[-1] == {"action": "crew", "row": 2, "delete": True}
    finally:
        m.post_webhook = orig


def t_crew_requires_webhook():
    ctl = m.SetupControl(None, _hs_stub())
    assert "error" in ctl.crew_set(1, name="Dana")
    assert "error" in ctl.crew_delete(1)


def t_crew_set_reflects_in_crew_source():
    # End-to-end echo: a successful write updates the in-memory CrewSource so
    # /crew/data shows it before the next poll (name/flags), like schedule.
    pushes = []
    hs = _hs_stub()
    cs = m.CrewSource("http://crew")
    cs.rows = [("Alice", True, False, False, False, "")]  # 1 existing data row (6-tuple)
    ctl = m.SetupControl("http://push", hs, crew_source=cs)
    def fake_post(url, payload, timeout=10):
        pushes.append(payload)
        return b'{"ok": true, "action": "crew", "v": 7}'
    m.post_webhook, orig = fake_post, m.post_webhook
    try:
        assert ctl.crew_set(2, name="Bob", director=False, producer=True,
                            commentator=True, discord="bob_d").get("ok")
        assert cs.get() == [("Alice", True, False), ("Bob", False, True)]  # appended
        assert cs.get_full()[1] == ("Bob", False, True, True, False, "bob_d")  # full echo
        assert ctl.crew_set(1, name="Alice", director=False, producer=False).get("ok")
        assert cs.get()[0] == ("Alice", False, False)                     # edited in place
        assert ctl.crew_delete(2).get("ok")
        assert cs.get() == [("Alice", False, False)]                      # deleted
    finally:
        m.post_webhook = orig


# ---------- Apps Script <-> relay SETUP_FIELDS parity (#331 follow-up) ----------
# Every setup-write field the relay can push (the SETUP_FIELDS header strings)
# must appear in the deployed Apps Script's SETUP_FIELDS allowlist, or
# writeSetup() throws "unknown setup field: <name>" and the webhook write
# silently fails. The reference script lives only as a code block in the wiki, so
# nothing enforced parity -- #331 added the Flag setup field to the relay but not
# to the script, shipping the Sheet write path dead. This couples the two lists.
SHEET_WEBHOOK_DOC = os.path.join(ROOT, "src", "docs", "wiki", "Sheet-Webhook.md")


def _documented_apps_script_setup_fields():
    import re
    with open(SHEET_WEBHOOK_DOC, encoding="utf-8") as fh:
        text = fh.read()
    match = re.search(r"const\s+SETUP_FIELDS\s*=\s*\[([^\]]*)\]", text)
    assert match, "SETUP_FIELDS array not found in the Sheet-Webhook.md Apps Script"
    return [s for s in re.findall(r"'([^']*)'", match.group(1))]


def t_setup_fields_parity_relay_vs_apps_script():
    relay_headers = {header for header, _hud_key in m.SETUP_FIELDS.values()}
    documented = set(_documented_apps_script_setup_fields())
    assert relay_headers == documented, (
        "relay SETUP_FIELDS and the documented Apps Script SETUP_FIELDS drifted: "
        f"relay-only={sorted(relay_headers - documented)}, "
        f"script-only={sorted(documented - relay_headers)}. Update the SETUP_FIELDS "
        "array in src/docs/wiki/Sheet-Webhook.md and redeploy the script.")


# ---------- webhook push retry + permanent-error predicate ----------

def t_webhook_error_permanent_predicate():
    assert m.webhook_error_permanent(m.WEBHOOK_OUTDATED_ERROR) is True
    assert m.webhook_error_permanent("webhook did not confirm: 'oops'") is False
    assert m.webhook_error_permanent(None) is False


def t_push_retry_succeeds_after_transient():
    # Two transient failures (a raise, then a 'did not confirm' body), then ok.
    calls = []
    ok_body = b'{"ok": true, "action": "setup"}'
    seq = [Exception("timeout"), b'{"ok": false}', ok_body]
    def fake_post(url, payload, timeout=10):
        calls.append(timeout)
        v = seq[len(calls) - 1]
        if isinstance(v, Exception):
            raise v
        return v
    ok, err, body = m.push_webhook_retrying(
        "http://push", {"action": "setup"}, "setup",
        post=fake_post, sleep=lambda d: None, rand=lambda: 0.0, now=lambda: 0.0)
    assert ok is True and err is None and body == ok_body
    assert len(calls) == 3
    assert calls[0] == m.WEBHOOK_RETRY_TIMEOUT_S    # lower per-attempt timeout used


def t_push_retry_exhausts_then_fails():
    def fake_post(url, payload, timeout=10):
        raise OSError("egress congested")
    slept = []
    ok, err, body = m.push_webhook_retrying(
        "http://push", {"a": 1}, None,
        post=fake_post, sleep=slept.append, rand=lambda: 0.0, now=lambda: 0.0)
    assert ok is False and body is None
    assert "OSError" in err and "push:" not in err   # UNPREFIXED
    assert slept == [0.5, 1.0]                        # base*2^n, 2 sleeps for 3 attempts


def t_push_retry_permanent_error_not_retried():
    calls = []
    def fake_post(url, payload, timeout=10):
        calls.append(1)
        return b'{"ok": true}'                        # ok:true but NO action echo
    ok, err, _ = m.push_webhook_retrying(
        "http://push", {"action": "setup"}, "setup",
        post=fake_post, sleep=lambda d: None, rand=lambda: 0.0, now=lambda: 0.0)
    assert ok is False
    assert err == m.WEBHOOK_OUTDATED_ERROR
    assert len(calls) == 1                            # permanent -> no retry


def t_push_retry_budget_cap_stops_early():
    calls = []
    def fake_post(url, payload, timeout=10):
        calls.append(1)
        raise OSError("slow")
    # now() jumps past the budget after the first attempt -> no 2nd attempt.
    ticks = iter([0.0, 999.0, 999.0, 999.0])
    ok, err, _ = m.push_webhook_retrying(
        "http://push", {"a": 1}, None, attempts=3,
        post=fake_post, sleep=lambda d: None, rand=lambda: 0.0, now=lambda: next(ticks))
    assert ok is False
    assert len(calls) == 1                            # budget cap stopped further attempts


# ---------- setup-assets media fill: template-driven scan ----------

def t_setup_media_fill_uses_template_scan():
    import placeholders as ph
    raw = '"file":"__RACECAST_MEDIA__/intermission.mp3"'
    assert "intermission.mp3" in ph.expected_media_from_template(raw)
    assert ph.media_placeholder_for("intermission.mp3") == ph.music_placeholder_path()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

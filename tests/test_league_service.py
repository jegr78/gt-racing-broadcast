#!/usr/bin/env python3
"""The league service client (#1253) against the simulated league service.
Run: python3 tests/test_league_service.py"""
import inspect
import json, logging, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import http_util  # noqa: E402
import league_service as ls  # noqa: E402
import league_sim  # noqa: E402
from http_server import NumericThreadingHTTPServer  # noqa: E402

ON = {"RACECAST_SIMULATION": "1"}
KEY = "fixture-league-key-1"


def _sim(**kw):
    return league_sim.LeagueSim(key=KEY, **kw).start()


def _cfg(sim, key=KEY):
    cfg, problem = ls.settings(sim.url, key, ON)
    assert cfg is not None, problem
    return cfg


def t_only_https_is_accepted_outside_simulation():
    assert ls.url_problem("https://league.example/racecast", {}) == ""
    assert "https" in ls.url_problem("http://league.example/racecast", {}), "plain http must be refused"
    assert "https" in ls.url_problem("http://127.0.0.1:9/racecast", {}), \
        "a loopback http URL outside simulation is refused too"
    assert ls.url_problem("http://127.0.0.1:9/racecast", ON) == "", "simulation takes its loopback fake"
    assert ls.url_problem("http://league.example/racecast", ON) != "", "simulation never takes a remote http URL"
    for bad in ("https://user:pw@league.example/x", "https://league.example/x?k=1",
                "https://league.example/x#f", "ftp://league.example/", "https://", "league.example"):
        assert ls.url_problem(bad, {}) != "", bad


def t_settings_need_both_values():
    assert ls.settings("", "", {}) == (None, ""), "no service configured is not a problem"
    cfg, problem = ls.settings("https://league.example/racecast/", "k", {})
    assert cfg == ls.Settings("https://league.example/racecast", "k") and problem == ""
    assert "KEY" in ls.settings("https://league.example/x", "", {})[1]
    assert "URL" in ls.settings("", "k", {})[1]
    cfg, problem = ls.settings("http://league.example/x", "k", {})
    assert cfg is None and "https" in problem


def _event(**over):
    return dict({"v": 1, "title": "Match", "races": [{"race": "leg-1", "title": "Leg 1"}],
                 "widgets": [{"id": "score", "title": "Score", "form": "score"}]}, **over)


def t_parse_event_keeps_a_valid_event():
    e = ls.parse_event(_event())
    assert e == {"v": 1, "title": "Match", "races": [{"race": "leg-1", "title": "Leg 1"}],
                 "widgets": [{"id": "score", "title": "Score", "form": "score"}]}, e


def t_parse_event_drops_what_is_beyond_a_limit():
    races = [{"race": f"r{i}"} for i in range(20)] + [{"race": "bad id"}, {"race": "r1"}, "x"]
    widgets = [{"id": f"w{i}", "title": "T", "form": "table"} for i in range(10)]
    widgets[0] = {"id": "w0", "title": "x" * 61, "form": "table"}
    widgets[1] = {"id": "W1", "title": "T", "form": "table"}
    widgets[2] = {"id": "w2", "title": "T", "form": "html"}
    e = ls.parse_event(_event(races=races, widgets=widgets))
    assert [r["race"] for r in e["races"]] == [f"r{i}" for i in range(16)], "16 races at most, bad ids dropped"
    assert [w["id"] for w in e["widgets"]] == [f"w{i}" for i in range(3, 10)][:8], e["widgets"]


def t_control_characters_drop_their_element():
    for bad in ("Leg\x1b[31m1", "Leg\x00", "Leg\x9b1", "Leg\x7f"):
        e = ls.parse_event(_event(races=[{"race": "leg-1", "title": bad}, {"race": "leg-2"}],
                                  widgets=[{"id": "w", "title": bad, "form": "score"}]))
        assert [r["race"] for r in e["races"]] == ["leg-2"] and e["widgets"] == [], (bad, e)
        v = ls.parse_widget_value({"values": [{"label": bad, "value": "1"}, {"label": "B", "value": "2"}]}, "score")
        assert v["values"] == [{"label": "B", "value": "2"}], (bad, v)


def t_an_element_is_dropped_whole_never_shortened():
    e = ls.parse_event(_event(races=[{"race": "leg-1", "title": "t" * 61}, {"race": "leg-2", "title": 5},
                                     {"race": "leg-3", "title": None}, {"race": "leg-4"}]))
    assert [r["race"] for r in e["races"]] == ["leg-4"], "a race with a bad title is dropped, not kept untitled"
    assert ls.parse_event(_event(title="t" * 81)) is None, "an event with a bad title does not parse"
    assert ls.parse_event(_event(title=None)) is None
    assert ls.parse_event(_event(title="")) is not None
    v = ls.parse_widget_value({"rows": [{"rank": 1, "name": "A", "value": None}, {"rank": 2, "name": "B", "value": ""},
                                        {"rank": 3, "name": "C"}]}, "table")
    assert v["rows"] == [{"rank": 2, "name": "B", "value": ""}], v
    v = ls.parse_widget_value({"values": [{"label": "A", "value": None}, {"label": "B", "value": "1"}]}, "score")
    assert v["values"] == [{"label": "B", "value": "1"}], v
    for bad in ({"provisional": "yes"}, {"values": {}}, {"rows": "x"}, {"value_title": "v" * 17},
                {"value_title": None}):
        assert ls.parse_widget_value(bad) is None, bad
    widgets = [{"id": "a", "title": "A", "form": "score", "sample": "x"},
               {"id": "b", "title": "B", "form": "score", "sample": {"provisional": 1}},
               {"id": "c", "title": "C", "form": "score"}]
    assert [w["id"] for w in ls.parse_event(_event(widgets=widgets))["widgets"]] == ["c"], \
        "a widget with a bad sample is dropped"


def t_a_query_or_fragment_mark_is_refused_even_empty():
    for bad in ("https://league.example/r?", "https://league.example/r#", "https://league.example/r?a=1"):
        assert ls.url_problem(bad, {}) != "", bad


def t_settings_never_show_the_key():
    cfg = ls.Settings("https://league.example/r", KEY)
    assert KEY not in repr(cfg) and KEY not in str(cfg) and "league.example" in repr(cfg)
    assert KEY not in repr([cfg]) and cfg.key == KEY


def t_parse_event_refuses_a_body_off_the_contract():
    for doc in (None, [], {"v": 2, "races": [{"race": "a"}]}, {"v": True, "races": [{"race": "a"}]},
                {"v": 1, "races": []}, {"v": 1, "races": [{"race": "bad id"}]}, {"v": 1}):
        assert ls.parse_event(doc) is None, doc


def t_widget_values_are_bounded_and_cut_to_their_form():
    rows = [{"rank": i + 1, "name": f"D{i}", "value": str(i)} for i in range(70)]
    rows[0]["name"] = "n" * 41
    rows[1]["rank"] = True
    doc = {"v": 1, "widgets": {
        "table": {"rows": rows, "value_title": "Pts", "values": [{"label": "x", "value": "1"}]},
        "score": {"values": [{"label": "A", "value": "12345678901234567"}, {"label": "B", "value": "3"}]
                  + [{"label": f"L{i}", "value": "1"} for i in range(10)], "provisional": True},
        "unknown": {"values": []}, "Bad Id": {}}}
    v = ls.parse_widget_values(doc, {"table": "table", "score": "score"})
    assert set(v) == {"table", "score"}, "widgets the event did not list are dropped"
    assert len(v["table"]["rows"]) == 64 and v["table"]["rows"][0]["rank"] == 3, \
        "64 rows at most; an over-long name and a bool rank drop their row"
    assert "values" not in v["table"], "a table carries no score values"
    assert v["score"]["values"][0] == {"label": "B", "value": "3"} and len(v["score"]["values"]) == 8
    assert v["score"]["provisional"] is True
    assert ls.parse_widget_values({"v": 1, "widgets": []}) is None
    assert ls.parse_widget_values({"v": 1, "widgets": {f"w{i}": {} for i in range(12)}}).keys() == \
        {f"w{i}" for i in range(8)}, "eight widgets at most"


def t_result_status_keeps_the_graphic_only_on_the_service_host():
    url = "https://league.example/racecast"
    s = ls.parse_result_status({"v": 1, "status": "stored", "message": "ok",
                                "link": "https://league.example/r/1",
                                "graphic_url": "https://league.example/r/1.png"}, url)
    assert s == {"status": "stored", "message": "ok", "link": "https://league.example/r/1",
                 "graphic_url": "https://league.example/r/1.png"}
    s = ls.parse_result_status({"v": 1, "status": "pending", "link": "http://league.example/r",
                                "graphic_url": "https://evil.example/x.png"}, url)
    assert s == {"status": "pending", "message": "", "link": None, "graphic_url": None}, s
    for other in ("https://league.example:8443/r/1.png", "http://league.example/r/1.png",
                  "https://league.example:99999/x.png", "https://LEAGUE.example.evil/x.png"):
        s = ls.parse_result_status({"v": 1, "status": "stored", "graphic_url": other}, url)
        assert s["graphic_url"] is None, f"{other} is not the service's own scheme, host and port"
    s = ls.parse_result_status({"v": 1, "status": "stored",
                                "graphic_url": "https://League.Example:443/r/1.png"}, url)
    assert s["graphic_url"] == "https://League.Example:443/r/1.png", "the default port is the service's port"
    sim = "http://127.0.0.1:5123/racecast"
    s = ls.parse_result_status({"v": 1, "status": "stored", "graphic_url": "http://127.0.0.1:5123/g.png"}, sim)
    assert s["graphic_url"] == "http://127.0.0.1:5123/g.png", "the loopback simulation serves its own graphic"
    s = ls.parse_result_status({"v": 1, "status": "stored", "graphic_url": "http://127.0.0.1:5124/g.png"}, sim)
    assert s["graphic_url"] is None, "another port on the same host is another service"
    assert ls.parse_result_status({"v": 1, "status": "stored", "message": "m" * 201}, url) is None, \
        "a status with an over-long message does not parse"
    assert ls.parse_result_status({"v": 1, "status": "done"}, url) is None


def t_fetch_event_sends_key_and_user_agent():
    with _sim() as sim:
        try:
            e = ls.fetch_event(_cfg(sim), league_sim.DEFAULT_REF)
        except ls.ServiceError as exc:
            e = exc
        assert sim.unauthorized == 0, f"the call must carry the bearer key: {e}"
        assert [r["race"] for r in e["races"]] == ["leg-1", "leg-2"]
        assert [w["id"] for w in e["widgets"]] == ["match-score", "standings"]
        (call,) = sim.calls()
        assert call["call"] == "event" and call["ref"] == league_sim.DEFAULT_REF
        assert call["user_agent"] == http_util.RACECAST_UA, call["user_agent"]


def t_a_wrong_key_fails_without_naming_it():
    with _sim() as sim:
        try:
            ls.fetch_event(_cfg(sim, key="wrong-key-value"), league_sim.DEFAULT_REF)
            raise AssertionError("a refused key must raise")
        except ls.ServiceError as e:
            assert "LEAGUE_SERVICE_KEY" in str(e) and "wrong-key-value" not in str(e), str(e)
        assert sim.unauthorized == 1 and sim.calls() == []


def t_an_unknown_ref_and_a_bad_ref_fail():
    with _sim() as sim:
        for ref, needle in (("NOPE", "404"), ("", "empty"), ("x" * 129, "longer"), ("a\nb", "control")):
            try:
                ls.fetch_event(_cfg(sim), ref)
                raise AssertionError(ref)
            except ls.ServiceError as e:
                assert needle in str(e), (ref, str(e))


def t_no_redirect_is_followed():
    hits = []

    class Elsewhere(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_a):
            pass

    other = NumericThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        with _sim() as sim:
            errors = {}
            for status in (301, 302, 307, 308):
                sim.answer("event", status=status, body={},
                           location=f"http://127.0.0.1:{other.server_address[1]}/steal")
                try:
                    ls.fetch_event(_cfg(sim), league_sim.DEFAULT_REF)
                    errors[status] = None
                except ls.ServiceError as e:
                    errors[status] = str(e)
        assert hits == [], "the key reached the redirect target"
        for status, error in errors.items():
            assert error and "redirect" in error, (status, error)
    finally:
        other.shutdown()
        other.server_close()


def t_an_answer_off_the_contract_raises():
    deep = "[" * 100000 + "]" * 100000
    with _sim() as sim:
        for raw, needle in (("x" * (ls.MAX_BODY + 1), "KiB"), ("not json", "JSON"), (deep, "JSON"),
                            (json.dumps({"v": 1, "races": []}), "contract")):
            sim.answer("event", raw=raw)
            try:
                ls.fetch_event(_cfg(sim), league_sim.DEFAULT_REF)
                raise AssertionError(needle)
            except ls.ServiceError as e:
                assert needle in str(e), (needle, str(e))


def t_a_broken_http_answer_raises_a_service_error():
    import socket
    for answer in (b"garbage\r\n\r\n", b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{\"v\": 1"):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def serve(s=srv, a=answer):
            conn, _ = s.accept()
            conn.recv(65536)
            conn.sendall(a)
            conn.close()
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        cfg = ls.Settings(f"http://127.0.0.1:{srv.getsockname()[1]}/racecast", KEY)
        try:
            ls.fetch_event(cfg, "REF", timeout=2.0)
            raise AssertionError(f"{answer!r} must raise")
        except ls.ServiceError as e:
            assert KEY not in str(e), str(e)
        finally:
            t.join(2)
            srv.close()


def t_a_dripping_answer_is_cut_off_at_the_deadline():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def serve():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100000\r\n\r\n")
        try:
            end = time.monotonic() + 4.0   # a client without a deadline waits this long, then fails
            while not stop.wait(0.2) and time.monotonic() < end:
                conn.sendall(b" ")
        except OSError:
            pass  # the client hung up
        conn.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    cfg = ls.Settings(f"http://127.0.0.1:{srv.getsockname()[1]}/racecast", KEY)
    started = time.monotonic()
    try:
        ls.fetch_event(cfg, "REF", deadline_s=1.0)
        raise AssertionError("a dripping answer must not be waited for")
    except ls.ServiceError as e:
        took = time.monotonic() - started
        assert took < 3.0 and "s" in str(e), (took, str(e))
    finally:
        stop.set()
        t.join(2)
        srv.close()
    assert ls.READ_DEADLINE_S == 10.0


def t_an_unreachable_service_raises():
    sim = _sim()
    cfg = _cfg(sim)
    sim.stop()
    try:
        ls.fetch_event(cfg, league_sim.DEFAULT_REF, timeout=1.0)
        raise AssertionError("a stopped service must raise")
    except ls.ServiceError as e:
        assert "reach" in str(e) or "HTTP" in str(e), str(e)


SNAP = {"v": 1, "mode": "race", "stint": 2, "remaining": 1200, "track_name": "Spa", "state": "racing",
        "red_flag": False, "lap": 7, "delay_s": 30, "visible": True, "lobby_lap": 8,
        "fastest": {"entry": "e1", "driver": "Ann", "team": "A", "ms": 138000, "pick": "x"},
        "entries": [{"pos": 1, "entry": "e1", "number": "7", "driver": "Ann", "team": "A", "laps": 7,
                     "gap_s": 0, "interval_s": None, "lapped": 0, "status": "racing", "pit_stops": 1,
                     "last_lap_ms": 139000, "finished": False, "fuel_l": 40.1, "compound": "RM",
                     "x": 1.0, "z": 2.0, "color": "#fff", "stream_url": "https://yt/x"}]}


def t_live_state_is_an_allowlist():
    body = ls.live_state("REF", "leg-1", SNAP)
    assert body["v"] == 1 and (body["ref"], body["race"], body["mode"]) == ("REF", "leg-1", "race")
    assert (body["stint"], body["lap"], body["remaining"], body["track_name"]) == (2, 7, 1200, "Spa")
    assert body["fastest"] == {"entry": "e1", "driver": "Ann", "team": "A", "ms": 138000}
    (row,) = body["entries"]
    for banned in ("fuel_l", "compound", "x", "z", "color", "stream_url"):
        assert banned not in row, f"{banned} must never leave the machine"
    assert row["driver_ref"] == "" and row["pit_stops"] == 1 and row["number"] == "7"
    for banned in ("delay_s", "visible", "lobby_lap"):
        assert banned not in body, banned


def t_live_state_sends_null_only_where_the_contract_allows_it():
    snap = {"track_name": None, "lap": None,
            "fastest": {"entry": "e1", "driver": "Ann", "team": None, "ms": 90000},
            "entries": [{"pos": 1, "entry": "e1", "driver": "Ann", "class": None, "laps": None,
                         "gap_s": None, "best_ms": None}]}
    body = ls.live_state("REF", "leg-1", snap)
    assert body.get("track_name", "absent") is None and body.get("state", "absent") is None, \
        "a track or state not known yet goes out as null, as the contract allows"
    assert body["lap"] is None and body["fastest"] == {"entry": "e1", "driver": "Ann", "ms": 90000}
    (row,) = body["entries"]
    assert "class" not in row and "laps" not in row
    assert row["gap_s"] is None and row["best_ms"] is None, "nullable fields keep their null"


def t_live_state_rows_are_entries_in_qualifying():
    snap = {"mode": "qualifying", "stint": "Q", "qualifying": [{"pos": 1, "entry": "e1", "driver": "Ann",
                                                               "best_ms": 90000, "gap_ms": None}]}
    body = ls.live_state("REF", "leg-1", snap)
    assert body["mode"] == "qualifying" and body["entries"][0]["best_ms"] == 90000
    assert body["stint"] is None, "a stint that is not a number is sent as null"
    assert "qualifying" not in body


def t_post_live_and_results_round_trip_against_the_sim():
    with _sim() as sim:
        cfg = _cfg(sim)
        values = ls.post_live(cfg, ls.live_state(league_sim.DEFAULT_REF, "leg-1", SNAP),
                              {"match-score": "score", "standings": "table"})
        assert values["match-score"]["values"] == [{"label": "A", "value": "0"}], values
        assert values["standings"]["rows"] == [{"rank": 1, "name": "Ann", "value": "7"}]
        result = {"v": 1, "ref": league_sim.DEFAULT_REF, "race": "leg-1", "entries": []}
        s = ls.post_result(cfg, result)
        assert s["status"] == "pending" and s["link"].startswith("https://")
        assert ls.fetch_result(cfg, league_sim.DEFAULT_REF, "leg-1")["status"] == "pending"
        try:
            ls.post_live(cfg, ls.live_state(league_sim.DEFAULT_REF, "leg-9", SNAP))
            raise AssertionError("a race outside the event is refused by the service")
        except ls.ServiceError as e:
            assert "400" in str(e)


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class _Log:
    def __init__(self):
        self.lines = []

    def warning(self, msg, *a):
        self.lines.append(msg % a)

    info = warning


def t_client_backs_off_and_recovers():
    clock, log, outcomes, sent = _Clock(), _Log(), [], []
    cfg = ls.Settings("https://league.example/racecast", KEY)

    def post(c, body, forms):
        sent.append(clock.t)
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
    c = ls.Client(lambda: cfg, lambda: {"v": 1}, dict, post=post, clock=clock, log=log)
    outcomes[:] = [ls.ServiceError("boom")] * 6 + [{"w": {"values": []}}]
    waits = []
    for _ in range(7):
        waits.append(c.step())
        clock.t += waits[-1]
    assert waits[:6] == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0], f"backoff doubles up to 30 s: {waits}"
    assert len(sent) == 7, "one call per due step, none while backing off"
    assert log.lines.count("league service: boom") == 1, "a repeated error is logged once"
    assert c.status()["last_error"] is None and c.values() == {"w": {"values": []}}
    assert log.lines[-1] == "league service: reachable again"
    assert KEY not in json.dumps(c.status()) and KEY not in " ".join(log.lines)


def t_client_holds_a_value_30_seconds_then_hides_it():
    clock = _Clock()
    answers = [{"w": {"values": [{"label": "A", "value": "1"}]}}]
    c = ls.Client(lambda: ls.Settings("https://l.example", KEY), lambda: {"v": 1}, dict,
                  post=lambda *a: answers[0], clock=clock, log=_Log())
    c.step()
    answers[0] = {}
    clock.t += ls.HOLD_S
    c.step()
    assert "w" in c.values(), "a missing id keeps its last value until it expires"
    clock.t += 0.1
    assert c.values() == {}, "the value hides 30 s after its last good answer"


def t_client_needs_the_event_forms():
    forms = inspect.signature(ls.Client).parameters["forms_fn"]
    assert forms.default is inspect.Parameter.empty, "forms_fn must be required"


def t_client_without_a_service_sends_nothing():
    calls = []
    c = ls.Client(lambda: None, lambda: {"v": 1}, dict, post=lambda *a: calls.append(a), log=_Log())
    assert c.step() == ls.TICK_S and calls == []
    assert c.status()["configured"] is False
    c2 = ls.Client(lambda: ls.Settings("https://l.example", KEY), lambda: None, dict,
                   post=lambda *a: calls.append(a), log=_Log())
    c2.step()
    assert calls == [], "no body (no event loaded) sends nothing"


def t_client_thread_posts_live_state_to_the_sim():
    with _sim() as sim:
        cfg = _cfg(sim)
        c = ls.Client(lambda: cfg, lambda: ls.live_state(league_sim.DEFAULT_REF, "leg-1", SNAP),
                      lambda: {"match-score": "score", "standings": "table"}, log=_Log())
        stop = threading.Event()
        t = threading.Thread(target=c.run, args=(stop,), daemon=True)
        t.start()
        deadline = time.monotonic() + 5
        while not c.values() and time.monotonic() < deadline:
            time.sleep(0.05)
        c.wake()
        stop.set()
        c.wake()
        t.join(5)
        assert not t.is_alive(), "the client thread ends on stop"
        assert set(c.values()) == {"match-score", "standings"}, c.values()
        assert sim.calls(call="live")[0]["body"]["entries"][0]["driver_ref"] == ""


def t_event_file_round_trip_keeps_the_current_race():
    with tempfile.TemporaryDirectory() as d:
        path = ls.path_in(d)
        assert ls.read(path) is None
        event = ls.parse_event(_event(races=[{"race": "leg-1"}, {"race": "leg-2"}]))
        doc = ls.store(path, "REF", event)
        assert doc["current"] == "leg-1" and ls.read(path)["races"][1]["race"] == "leg-2"
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["current"] = "leg-2"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        assert ls.store(path, "REF", event)["current"] == "leg-2", "a reload keeps the current race"
        assert ls.store(path, "OTHER", event)["current"] == "leg-1", "another event starts at the first race"
        raw["current"] = "leg-9"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        assert ls.read(path) is None, "a current race outside the list is not trusted"
        ls.clear(path)
        ls.clear(path)
        assert not os.path.exists(path)


def t_the_finished_and_done_marks_survive_a_reload_of_the_same_event_only():
    with tempfile.TemporaryDirectory() as d:
        path = ls.path_in(d)
        assert ls.update(path, current="leg-2") is None, "no event, nothing to change"
        event = ls.parse_event(_event(races=[{"race": "leg-1"}, {"race": "leg-2"}]))
        doc = ls.store(path, "REF", event)
        assert (doc["finished"], doc["done"], doc["finished_at"]) == (False, False, None), doc
        doc = ls.update(path, current="leg-2", finished=True, finished_at=1700.5)
        assert (doc["current"], doc["finished"], doc["finished_at"]) == ("leg-2", True, 1700.5), doc
        try:
            ls.update(path, current="leg-9")
        except ValueError:
            pass  # expected
        else:
            raise AssertionError("a race outside the event is refused")
        ls.update(path, done=True)
        again = ls.store(path, "REF", event)
        assert again["finished"] and again["done"] and again["finished_at"] == 1700.5, \
            f"reloading the event keeps where it stands: {again}"
        other = ls.store(path, "OTHER", event)
        assert not other["finished"] and not other["done"] and other["current"] == "leg-1", other


def t_the_relay_reads_the_service_from_the_profile_or_the_simulation():
    with tempfile.TemporaryDirectory() as d:
        env_file = os.path.join(d, "profile.env")
        with open(env_file, "w", encoding="utf-8") as fh:
            fh.write("LEAGUE_SERVICE_URL=https://league.example/r\nLEAGUE_SERVICE_KEY='fixture-key'\n")
        cfg, problem = ls.relay_settings(env_file, {})
        assert cfg == ls.Settings("https://league.example/r", "fixture-key") and problem == "", (cfg, problem)
        assert ls.relay_settings(os.path.join(d, "missing.env"), {}) == (None, "")
        assert ls.relay_settings("", {}) == (None, "")
        sim = {"RACECAST_SIMULATION": "1", "RACECAST_SIM_LEAGUE_SERVICE_URL": "http://127.0.0.1:9/racecast",
               "RACECAST_SIM_LEAGUE_SERVICE_KEY": "sim-key"}
        assert ls.relay_settings(env_file, sim)[0] == ls.Settings("http://127.0.0.1:9/racecast", "sim-key"), \
            "simulation replaces the profile's service"
        assert ls.relay_settings(env_file, {"RACECAST_SIMULATION": "1"}) == (None, ""), \
            "simulation never reaches the real service"


def t_the_event_file_lock_waits_and_breaks_a_stale_lock():
    with tempfile.TemporaryDirectory() as d:
        path = ls.path_in(d)
        ls.store(path, "REF", ls.parse_event(_event(races=[{"race": "leg-1"}, {"race": "leg-2"}])))
        open(path + ".lock", "w").close()
        orig = ls.LOCK_WAIT_S
        ls.LOCK_WAIT_S = 0.2
        try:
            ls.update(path, current="leg-2")
        except OSError as e:
            assert "held" in str(e)
        else:
            raise AssertionError("a held lock keeps the update out")
        finally:
            ls.LOCK_WAIT_S = orig
        assert ls.read(path)["current"] == "leg-1"
        old = time.time() - ls.LOCK_STALE_S - 5
        os.utime(path + ".lock", (old, old))
        assert ls.update(path, current="leg-2")["current"] == "leg-2", "a crashed holder's lock is broken"
        assert not os.path.exists(path + ".lock")


def t_client_logs_carry_no_key():
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    h = H()
    ls.LOG.addHandler(h)
    try:
        with _sim() as sim:
            c = ls.Client(lambda: _cfg(sim, key="leaky-key-123"), lambda: {"v": 1}, dict, clock=_Clock())
            c.step()
    finally:
        ls.LOG.removeHandler(h)
    assert records and all("leaky-key-123" not in r for r in records), records


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

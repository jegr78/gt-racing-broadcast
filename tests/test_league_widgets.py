#!/usr/bin/env python3
"""League service live state, widgets and Director control (#1255): the profile's widget
list, the builder slots, the live sender against the simulated service, the relay routes
and the overlay renderer.
Run: python3 tests/test_league_widgets.py"""
import json, os, shutil, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import league_service as ls  # noqa: E402
import league_sim  # noqa: E402
import league_widgets as lw  # noqa: E402
import driver_race  # noqa: E402
import overlay_build as ob  # noqa: E402

KEY = "fixture-league-key-5"
ON = {"RACECAST_SIMULATION": "1"}
REF = league_sim.DEFAULT_REF
RENDER_JS = os.path.join(ROOT, "src", "ui", "league-widgets.js")
SCORE = {"id": "match-score", "title": "Match", "form": "score",
         "sample": {"values": [{"label": "Team A", "value": "0"}]}}
TABLE = {"id": "standings", "title": "Championship", "form": "table"}


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


def _cfg(sim):
    cfg, problem = ls.settings(sim.url, KEY, ON)
    assert cfg is not None, problem
    return cfg


SNAP = {"mode": "race", "state": "racing", "entries": [
    {"pos": 1, "entry": "e1", "driver": "Ann", "team": "A", "laps": 3},
    {"pos": 2, "entry": "e2", "driver": "Bob", "team": "B", "laps": 3}]}


def _live(d, sim=None, snap=None, clock=None, overlay_dir=None, **kw):
    """A Live over a loaded event in `d`; the snapshot is a mutable one-item list."""
    snap = snap if snap is not None else [SNAP]
    if sim is not None:
        ls.store(ls.path_in(d), REF, ls.fetch_event(_cfg(sim), REF))
    settings = (lambda: (_cfg(sim), "")) if sim is not None else (lambda: (None, ""))
    kw.setdefault("log", _Log())
    if clock is not None:
        kw["clock"] = clock
    return lw.Live(settings, lambda: snap[0], ls.path_in(d), overlay_dir=overlay_dir, **kw), snap


def _place(overlay_dir, page, *ids):
    os.makedirs(overlay_dir, exist_ok=True)
    with open(os.path.join(overlay_dir, f"layout-{page}.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "page": page, "slots": {lw.slot_id(i): {"left": 10} for i in ids}}, fh)


# The profile's widget list

def t_the_event_widget_list_is_kept_and_a_dropped_widget_stays_unlisted():
    with tempfile.TemporaryDirectory() as d:
        path = lw.path_in(d)
        lw.remember(path, [SCORE, TABLE])
        assert [(w["id"], w["listed"]) for w in lw.read(path)] == [("match-score", True), ("standings", True)]
        kept = lw.remember(path, [TABLE])
        assert [(w["id"], w["listed"]) for w in kept] == [("standings", True), ("match-score", False)], \
            "a widget the service stops listing stays in the profile, marked unlisted"
        assert lw.read(path)[1]["sample"] == SCORE["sample"], "its sample values stay for the builder"
        lw.remember(path, [SCORE])
        assert [(w["id"], w["listed"]) for w in lw.read(path)] == [("match-score", True), ("standings", False)]


def t_a_broken_or_hostile_widget_file_reads_as_nothing_usable():
    with tempfile.TemporaryDirectory() as d:
        path = lw.path_in(d)
        assert lw.read(path) == []
        for raw in ("{", "[]", '{"widgets": 5}'):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(raw)
            assert lw.read(path) == [], raw
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"widgets": [{"id": "../x", "title": "T", "form": "score"},
                                   {"id": "ok", "title": "T\u0000", "form": "score"},
                                   {"id": "ok", "title": "Fine", "form": "pie"},
                                   dict(SCORE, listed=False)]}, fh)
        assert [(w["id"], w["listed"]) for w in lw.read(path)] == [("match-score", False)], \
            "an id, title or form off the contract is dropped on read"


# Markup and builder slots

BASE = ('<html><head><style>body{margin:0}</style></head><body><div id="clock"></div>'
        '<script>window.x=1;</script></body></html>')


def t_markup_escapes_the_title_and_only_placed_widgets_reach_a_page():
    hostile = dict(SCORE, title='<img src=x onerror=alert(1)>"')
    page = lw.enhance_html(BASE, [hostile, TABLE], {lw.slot_id("match-score"): {}}, live=True)
    assert "<img" not in page and "&lt;img src=x onerror=alert(1)&gt;&quot;" in page, page
    assert page.count("data-league-widget=") == 1 and "standings" not in page
    assert 'id="league-widget-match-score"' in page and " hidden>" in page, "a widget starts hidden"
    assert page.count("data-league-live") == 1 and page.index("data-league-widget") < page.index("data-league-live")
    assert lw.enhance_html(BASE, [SCORE], {}) == BASE, "nothing placed leaves the page unchanged"
    try:
        lw.markup(dict(SCORE, id='x" onload="y'))
        raise AssertionError("an id off the contract never reaches the markup")
    except ValueError:
        pass


def t_the_widget_colours_follow_the_profiles_overlay_variables():
    page = lw.enhance_html(BASE, [SCORE], {lw.slot_id("match-score"): {}})
    (slot,) = ob.extract_slots(page)
    assert slot["vars"] == {"color": "var(--league-widget-text, #ffffff)",
                            "background": "var(--league-widget-bg, rgba(0,0,0,.8))",
                            "borderColor": "var(--league-widget-accent, #27d34d)"}, \
        "the builder saves the colours as references, so a profile's :root variables restyle the widget"
    css = ob.compile_overlay_css({"slots": {slot["id"]: {"left": 10, **slot["vars"]}},
                                  "customCss": ":root { --league-widget-bg: #102030; }"}, [slot])
    assert "background: var(--league-widget-bg, rgba(0,0,0,.8))" in css and "--league-widget-bg: #102030" in css, css


def t_the_builder_offers_each_widget_as_a_slot_and_compiles_its_style():
    page = lw.enhance_html(BASE, [SCORE, TABLE], {lw.slot_id(w["id"]): {} for w in (SCORE, TABLE)})
    slots = {s["id"]: s for s in ob.extract_slots(page)}
    assert set(slots) == {"league-widget-match-score", "league-widget-standings"}, slots
    assert slots["league-widget-match-score"]["label"] == "League: Match"
    assert {"left", "fontFamily", "background", "visible"} <= set(slots["league-widget-standings"]["props"])
    css = ob.compile_overlay_css({"slots": {"league-widget-standings": {"left": 40, "top": 900, "color": "#ff0",
                                                                        "presentation": "bar"}}},
                                 list(slots.values()))
    assert "#league-widget-standings { left: 40px; top: 900px; color: #ff0; }" in css, css


def t_live_pages_carry_the_placed_widgets_and_survive_a_broken_layout():
    with tempfile.TemporaryDirectory() as d:
        overlay = os.path.join(d, "overlay")
        lw.remember(lw.path_in(d), [SCORE, TABLE])
        _place(overlay, "hud", "standings")
        page = lw.live_html(BASE, overlay, "hud")
        assert "league-widget-standings" in page and "league-widget-match-score" not in page
        assert lw.live_html(BASE, overlay, "intermission") == BASE, "a scene without a layout is untouched"
        _place(overlay, "drivers", "standings")
        assert lw.live_html(BASE, overlay, "drivers") == BASE, "the driver dashboard has no league widgets"
        with open(os.path.join(overlay, "layout-hud.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        assert lw.live_html(BASE, overlay, "hud") == BASE, "a broken layout never takes the overlay down"
        lw.remember(lw.path_in(d), [SCORE])
        _place(overlay, "hud", "standings")
        assert "league-widget-standings" in lw.live_html(BASE, overlay, "hud"), \
            "a widget the service no longer lists stays placed"
        assert lw.placements(overlay) == {"standings": ["hud"]}


def t_loading_the_event_keeps_the_widget_list_in_the_profile():
    import importlib.util
    spec = importlib.util.spec_from_file_location("racecast", os.path.join(ROOT, "src", "racecast.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    saved = os.environ.get("RACECAST_SIMULATION")
    os.environ["RACECAST_SIMULATION"] = "1"
    try:
        with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
            rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="S", league_service_url=sim.url,
                                       league_service_key=KEY, profile_dir=d)
            r = m.league_event_load_data(REF, rc=rc, path=ls.path_in(os.path.join(d, "rt")))
            assert r["ok"], r
            assert [(w["id"], w["listed"]) for w in lw.read(lw.path_in(d))] == \
                [("match-score", True), ("standings", True)]
            sim.events[REF] = dict(sim.events[REF], widgets=sim.events[REF]["widgets"][1:])
            assert m.league_event_load_data(REF, rc=rc, path=ls.path_in(os.path.join(d, "rt")))["ok"]
            assert [(w["id"], w["listed"]) for w in lw.read(lw.path_in(d))] == \
                [("standings", True), ("match-score", False)]
    finally:
        if saved is None:
            os.environ.pop("RACECAST_SIMULATION", None)
        else:
            os.environ["RACECAST_SIMULATION"] = saved


def t_a_widget_list_that_cannot_be_saved_has_its_own_message():
    import importlib.util
    spec = importlib.util.spec_from_file_location("racecast", os.path.join(ROOT, "src", "racecast.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    saved = os.environ.get("RACECAST_SIMULATION")
    os.environ["RACECAST_SIMULATION"] = "1"
    try:
        with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
            rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="S", league_service_url=sim.url,
                                       league_service_key=KEY, profile_dir=d)
            blocked = os.path.join(d, "league-widgets.json")
            os.makedirs(blocked)                       # a folder where the file belongs
            r = m.league_event_load_data(REF, rc=rc, path=ls.path_in(os.path.join(d, "rt")), widgets_path=blocked)
            assert not r["ok"] and r["error"].startswith("loaded the event, but could not save its widget list"), r
            assert ls.read(ls.path_in(os.path.join(d, "rt")))["ref"] == REF, "the event itself is loaded"
    finally:
        if saved is None:
            os.environ.pop("RACECAST_SIMULATION", None)
        else:
            os.environ["RACECAST_SIMULATION"] = saved


# The live sender against the simulated service

def t_the_body_is_cut_from_the_snapshot_for_the_loaded_events_current_race():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, snap = _live(d, sim)
        body = live.body()
        assert (body["ref"], body["race"], body["mode"]) == (REF, "leg-1", "race")
        assert [r["entry"] for r in body["entries"]] == ["e1", "e2"]
        snap[0] = None
        assert live.body() is None, "no snapshot yet: nothing to send"
    with tempfile.TemporaryDirectory() as d:
        live, _snap = _live(d)
        assert live.body() is None, "no event loaded: nothing to send"


def t_the_live_body_is_undelayed_and_carries_race_controls_estimate():
    import test_driver_race as tdr
    svc, clock, _path, ctx = tdr._roster_service()
    svc.set_settings({"delay_s": 30})
    svc.tick()
    order = [r["entry"] for r in svc.live()["entries"]]
    assert order[-1] == "X", order
    svc.estimate("rc:Cy", "X", "A", 1.0, ahead=True)
    clock[0] += 0.5
    svc.tick()
    body = ls.live_state(REF, "leg-1", svc.live())
    assert [r["entry"] for r in body["entries"]][0] == "X", "the estimate is in the body at once"
    assert [r["entry"] for r in svc.dashboard()["entries"]] == order, "the delayed dashboard still shows the old order"
    x = body["entries"][0]
    assert x["status"] == "est" and x["number"] == "9" and x["team"] == "Grey Team" and x["driver_ref"] == ""
    assert all("best_ms" in r for r in body["entries"]), "each row carries its best counted lap"
    for row in body["entries"]:
        for banned in ("fuel_l", "compound", "x", "z", "color", "brand", "stream_url", "stint_laps"):
            assert banned not in row, f"{banned} never leaves the machine"
    ctx["mode"], ctx["segment"] = "qualifying", "Q"
    svc.tick()
    body = ls.live_state(REF, "leg-1", svc.live())
    assert body["mode"] == "qualifying" and "qualifying" not in body and isinstance(body["entries"], list)


def t_live_state_goes_out_every_two_seconds_and_at_once_on_a_position_change():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, snap = _live(d, sim, clock=clock)
        live.observe(snap[0])
        wait = live.client.step()
        assert 1.9 <= wait <= ls.TICK_S and len(sim.calls(call="live")) == 1, wait
        live.client._wake.clear()
        live.observe(snap[0])
        assert not live.client._wake.is_set(), "the same order does not send early"
        snap[0] = dict(SNAP, entries=[dict(SNAP["entries"][1], pos=1), dict(SNAP["entries"][0], pos=2)])
        live.observe(snap[0])
        assert live.client._wake.is_set(), "a position change wakes the sender at once"
        assert live.client.step() == ls.MIN_GAP_S and len(sim.calls(call="live")) == 1, \
            "a wake right after a send waits for the minimum gap"
        clock.t += ls.MIN_GAP_S
        live.client.step()
        calls = sim.calls(call="live")
        assert [r["entry"] for r in calls[-1]["body"]["entries"]] == ["e2", "e1"], calls[-1]["body"]
        live.client._wake.clear()
        snap[0] = dict(snap[0], mode="qualifying", qualifying=snap[0]["entries"])
        live.observe(snap[0])
        assert live.client._wake.is_set(), "a switch to qualifying is a new running order"
        clock.t += ls.MIN_GAP_S
        live.client.step()
        body = sim.calls(call="live")[-1]["body"]
        assert body["mode"] == "qualifying" and [r["entry"] for r in body["entries"]] == ["e2", "e1"]


def t_a_value_change_reaches_the_pages_only_once_the_director_shows_it():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, snap = _live(d, sim, clock=clock)
        live.client.step()
        assert live.data() == {"ok": True, "widgets": []}, "default hidden"
        assert live.set("match-score", "show") == (True, "match-score")
        (w,) = live.data()["widgets"]
        assert (w["id"], w["form"], w["title"]) == ("match-score", "score", "Match")
        first = w["value"]["values"]
        snap[0] = dict(SNAP, entries=[dict(SNAP["entries"][1], pos=1), dict(SNAP["entries"][0], pos=2)])
        clock.t += ls.TICK_S
        live.client.step()
        second = live.data()["widgets"][0]["value"]["values"]
        assert first != second and second[0] == {"label": "B", "value": "1"}, (first, second)
        assert live.set("2", "toggle") == (True, "standings") and len(live.data()["widgets"]) == 2
        assert live.set("match-score", "toggle") == (True, "match-score")
        assert [w["id"] for w in live.data()["widgets"]] == ["standings"]


def t_loading_an_event_hides_every_widget_again():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        live.client.step()
        live.set("1", "show")
        assert live.data()["widgets"], "shown"
        live.client.step()
        ls.update(ls.path_in(d), current="leg-2")
        assert live.data()["widgets"], "a race change keeps the switch"
        ls.store(ls.path_in(d), REF, ls.fetch_event(_cfg(sim), REF), now=4e9)
        assert live.data()["widgets"] == [], "a reload of the event starts hidden again"
        assert [r["shown"] for r in live.director()["widgets"]] == [False, False]


def t_a_failed_read_of_the_event_keeps_the_switch():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        live.client.step()
        live.set("1", "show")
        real = ls.read
        ls.read = lambda path: None          # a file caught mid-write by another process
        try:
            assert [w["id"] for w in live.data()["widgets"]] == ["match-score"], "a read error is no new event"
        finally:
            ls.read = real
        assert live.data()["widgets"] and live.client.held(), "the switch and the values stay"
        ls.clear(ls.path_in(d))
        assert live.data()["widgets"] == [] and live.client.held() == {}, "event stop: the file is gone"


def t_a_new_event_forgets_the_values_of_the_old_one():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        live.client.step()
        assert "match-score" in live.client.held()
        ls.store(ls.path_in(d), "OTHER-MATCH", dict(ls.fetch_event(_cfg(sim), REF)), now=4e9)
        live.set("1", "show")
        assert live.client.held() == {} and live.data()["widgets"] == [], \
            "the same widget id of another event never shows the old event's value"


def t_the_switch_refuses_a_widget_the_event_does_not_list():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        for target, action in (("3", "show"), ("0", "show"), ("other", "show"), ("../x", "show"),
                               ("match-score", "flip"), ("x" * 40, "show"), ("\u00b2", "show"),
                               ("\u0661", "show"), ("01", "show"), ("9", "show")):
            ok, why = live.set(target, action)
            assert not ok and why, (target, action)
        assert live.set("9", "show") == (False, "the loaded event lists no such widget"), \
            "an event holds at most 8 widgets, so 9 is no place"


def t_a_slow_service_never_holds_the_dashboard_tick():
    class _Relay:
        def __init__(self):
            self.ticks = 0

        def _tick_ok(self, name):
            if name == "driver dashboard":
                self.ticks += 1

        def _warn_tick_error(self, name, e):
            raise AssertionError(f"{name}: {e}")

    class _Svc:
        def tick(self):
            pass

        def newest(self):
            return SNAP

        def race_state(self):
            return None

    import importlib.util
    spec = importlib.util.spec_from_file_location("feeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    feeds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(feeds)
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        sim.fail("call:live", delay=2.5)
        stop, relay = threading.Event(), _Relay()
        sender = threading.Thread(target=live.run, args=(stop,), daemon=True)
        ticker = threading.Thread(target=feeds.dashboard_ticks, args=(relay, _Svc(), stop, 0.05, None, live),
                                  daemon=True)
        started = time.monotonic()
        sender.start()
        ticker.start()
        while not sim.calls(call="live") and time.monotonic() - started < 8:
            time.sleep(0.05)
        elapsed = time.monotonic() - started
        stop.set()
        live.client.wake()
        ticker.join(2)
        sender.join(5)
        assert elapsed >= 2.4, f"the service answered only after its delay ({elapsed:.2f} s)"
        assert relay.ticks >= 20, f"the dashboard ticked on while the service took {elapsed:.1f} s: {relay.ticks}"
        assert live.client.held(), "the slow answer still lands"


def t_a_dead_service_holds_the_value_30_seconds_then_names_the_reason():
    with tempfile.TemporaryDirectory() as d:
        clock = _Clock()
        overlay = os.path.join(d, "overlay")
        _place(overlay, "hud", "match-score")
        with league_sim.LeagueSim(key=KEY) as sim:
            live, _snap = _live(d, sim, clock=clock, overlay_dir=overlay)
            live.set("1", "show")
            live.client.step()
            sim.stop()
            clock.t += 5
            live.client.step()
        row = live.director()["widgets"][0]
        assert row["live"] and row["reason"] == "" and live.data()["widgets"], "held after a failure"
        assert live.data()["widgets"][0]["holdFor"] == 25.0
        clock.t += ls.HOLD_S - 5 + 1
        assert live.data()["widgets"] == [], "31 s after the last good answer the widget hides"
        d_row = live.director()["widgets"][0]
        assert d_row["shown"] and not d_row["live"], d_row
        assert d_row["reason"].startswith("no good answer from the league service for 31 s: cannot reach"), d_row
        assert KEY not in json.dumps(live.director())


def t_an_oversized_or_unparseable_answer_keeps_the_last_good_value():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, _snap = _live(d, sim, clock=clock)
        live.set("1", "show")
        live.client.step()
        good = live.data()["widgets"][0]["value"]
        for raw, needle in (("x" * (ls.MAX_BODY + 1), "256 KiB"), ("not json", "not JSON"),
                            (json.dumps({"v": 2, "widgets": {}}), "contract"),
                            (json.dumps({"v": 1, "widgets": {"match-score": {"values": "x"}}}), None)):
            sim.answer("live", raw=raw)
            clock.t += 3
            live.client._next_attempt = 0
            live.client.step()
            st = live.client.status()
            if needle:
                assert needle in (st["last_error"] or ""), (needle, st)
            assert live.data()["widgets"][0]["value"] == good, f"{needle}: the last good value stays"


def t_markup_in_a_label_arrives_as_plain_text():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        label = '<img src=x onerror="alert(1)">'
        sim.answer("live", body={"v": 1, "widgets": {"match-score": {"values": [{"label": label, "value": "1"}]}}})
        live.set("1", "show")
        live.client.step()
        (w,) = live.data()["widgets"]
        assert w["value"]["values"][0]["label"] == label, "the relay passes the text as it came"
        out = _render([{"id": "match-score", "form": "score"}], {"widgets": [w]})
        if out is not None:
            assert out["tags"] == ["div", "div", "div", "span", "span"], out
            assert out["texts"][3:] == [label, "1"], out


def t_the_key_never_reaches_a_page_or_the_director():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        live.set("1", "show")
        live.client.step()
        assert KEY not in json.dumps(live.data()) and KEY not in json.dumps(live.director())


def t_without_a_service_the_director_says_why():
    with tempfile.TemporaryDirectory() as d:
        live, _snap = _live(d, None)
        ls.store(ls.path_in(d), REF, {"v": 1, "title": "", "races": [{"race": "leg-1", "title": ""}],
                                      "widgets": [SCORE]})
        live.client.step()
        (row,) = live.director()["widgets"]
        assert row["reason"] == "no league service configured" and live.data()["widgets"] == []


def t_the_director_sees_a_dropped_widget_that_stays_placed():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        overlay = os.path.join(d, "overlay")
        lw.remember(lw.path_in(d), [SCORE, TABLE, dict(TABLE, id="old-table", title="Old")])
        _place(overlay, "hud", "old-table", "match-score")
        live, _snap = _live(d, sim, overlay_dir=overlay)
        live.client.step()
        rows = {r["id"]: r for r in live.director()["widgets"]}
        assert rows["old-table"]["listed"] is False and rows["old-table"]["reason"].startswith("the league service no longer")
        assert rows["standings"]["reason"].startswith("not placed"), rows["standings"]
        assert rows["match-score"]["reason"] == "" and rows["match-score"]["placed"] == ["hud"]
        assert not live.set("old-table", "show")[0], "an unlisted widget cannot go on air"


# The public timing page (#1256)

def _timing_env(d, value):
    env = os.path.join(d, "profile.env")
    with open(env, "w", encoding="utf-8") as fh:
        fh.write(f"NAME=x\n{lw.TIMING_KEY}={value}\n" if value is not None else "NAME=x\n")
    return env


def t_the_profile_switch_decides_the_timing_block_independent_of_the_director():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        env = _timing_env(d, None)
        live, _snap = _live(d, sim, env_path=env)
        live.client.step()
        assert live.public(0) is None, "default off: no block"
        for value in ("0", "off", "maybe", ""):
            _timing_env(d, value)
            os.utime(env, (len(value) + 1, len(value) + 1))
            assert live.public(0) is None, f"{value!r} is off"
        _timing_env(d, "1")
        os.utime(env, (90, 90))
        block = live.public(0)
        assert [w["id"] for w in block] == ["match-score", "standings"], \
            f"every widget of the event, none shown by the Director: {block}"
        assert live.data()["widgets"] == [], "the timing page switch never puts a widget on air"
        os.remove(env)
        assert live.public(0) is None, "an unreadable profile.env is off"


def t_the_timing_block_is_an_allowlist_of_id_form_title_and_value():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim, env_path=_timing_env(d, "1"))
        sim.answer("live", body={"v": 1, "widgets": {
            "match-score": {"provisional": True, "values": [{"label": "A", "value": "1"}]},
            "standings": {"value_title": "Pts", "rows": [{"rank": 1, "name": "Ann", "value": "25"}]}}})
        live.client.step()
        live.set("1", "show")
        score, table = live.public(0)
        assert score == {"id": "match-score", "form": "score", "title": "Match",
                         "value": {"provisional": True, "values": [{"label": "A", "value": "1"}]}}, score
        assert table == {"id": "standings", "form": "table", "title": "Championship",
                         "value": {"value_title": "Pts", "rows": [{"rank": 1, "name": "Ann", "value": "25"}]}}, table
        assert json.dumps(live.public(0)) == json.dumps(live.public(0)), \
            "nothing in the block counts down, so an unchanged value keeps the body's ETag"
        assert KEY not in json.dumps(live.public(0))


def t_the_public_cut_keeps_only_the_allowlisted_fields():
    score = {"provisional": True, "extra": "x", "values": [{"label": "A", "value": "1", "color": "#f00"}]}
    table = {"value_title": "Pts", "note": "x", "rows": [{"rank": 1, "name": "Ann", "value": "2", "ref": "d1"}],
             "values": [{"label": "A", "value": "1"}]}
    assert lw.public_value("score", score) == {"provisional": True, "values": [{"label": "A", "value": "1"}]}
    assert lw.public_value("table", table) == {"value_title": "Pts", "rows": [{"rank": 1, "name": "Ann", "value": "2"}]}, \
        "a field the parser might keep one day never reaches the public body"
    assert lw.public_value("chart", score) == {}
    rows = [{"rank": i, "name": f"D{i}", "value": "1"} for i in range(1, ls.MAX_ROWS + 1)]
    assert [r["rank"] for r in lw.public_value("table", {"rows": rows})["rows"]] == list(range(1, lw.PUBLIC_ROWS + 1)), \
        f"the public body keeps the first {lw.PUBLIC_ROWS} rows of a table"


def t_a_widget_value_reaches_the_timing_page_delay_s_after_it_arrived():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, snap = _live(d, sim, clock=clock, env_path=_timing_env(d, "1"))
        live.client.step()                                      # t=100: Ann leads
        first = live.public(0)[0]["value"]
        snap[0] = dict(SNAP, entries=[dict(SNAP["entries"][1], pos=1), dict(SNAP["entries"][0], pos=2)])
        clock.t = 110.0
        live.client.step()                                      # t=110: Bob leads
        second = live.public(0)[0]["value"]
        assert first != second, (first, second)
        clock.t = 159.9
        assert live.public(60) == [], "no answer is 60 s old yet: nothing ahead of the delayed timing"
        clock.t = 160.0
        assert live.public(60)[0]["value"] == first, "60 s after it arrived"
        clock.t = 169.9
        assert live.public(60)[0]["value"] == first, "the overtake waits for the picture"
        clock.t = 170.0
        assert live.public(60)[0]["value"] == second


def t_the_hold_shifts_with_the_delay_when_the_service_dies():
    with tempfile.TemporaryDirectory() as d:
        clock = _Clock()
        with league_sim.LeagueSim(key=KEY) as sim:
            live, _snap = _live(d, sim, clock=clock, env_path=_timing_env(d, "1"))
            live.client.step()                                  # last good answer at t=100
            sim.stop()
            clock.t = 105.0
            live.client.step()
        clock.t = 100.0 + 60 + ls.HOLD_S
        assert live.public(60), "the delayed page holds the value as long as the live one did"
        clock.t = 100.0 + 60 + ls.HOLD_S + 0.1
        assert live.public(60) == [], "then it hides, delay_s after the live pages"
        clock.t = 100.0 + 200
        assert live.public(driver_race.MAX_DELAY_S) == [], "never older than its hold, whatever the delay"


def t_a_gap_longer_than_the_hold_stays_a_gap_for_an_unchanged_value():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, _snap = _live(d, sim, clock=clock, env_path=_timing_env(d, "1"))
        live.client.step()                                      # t=100
        clock.t = 150.0
        live.client.step()                                      # the same value again after 50 s of silence
        clock.t = 185.0
        assert live.public(50) == [], "at t=135 the value had been gone for 35 s, longer than the hold"
        assert live.public(30), "and at t=155 it was back"


def t_a_new_event_forgets_the_delayed_values_too():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, _snap = _live(d, sim, clock=clock, env_path=_timing_env(d, "1"))
        live.client.step()
        clock.t += 30
        assert live.public(30), "held"
        ls.store(ls.path_in(d), REF, ls.fetch_event(_cfg(sim), REF), now=4e9)
        assert live.public(30) == [], "a reload of the event drops what the old one answered"


def t_a_delay_beyond_the_history_shows_nothing():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, _snap = _live(d, sim, clock=clock, env_path=_timing_env(d, "1"))
        for _ in range(100):
            live.client.step()
            clock.t += ls.TICK_S
        limit = ls.HISTORY_S - ls.HOLD_S
        assert live.client.delayed(limit), "the longest covered delay still shows"
        assert live.client.delayed(limit + 1) == {} and live.client.delayed(10 ** 6) == {}, \
            "a delay the history cannot cover fails closed instead of showing a newer value"


def t_wake_bursts_keep_the_history_small():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, snap = _live(d, sim, clock=clock)
        sim.answer("live", body={"v": 1, "widgets": {"standings": {"rows": [
            {"rank": i, "name": f"D{i}", "value": "1"} for i in range(1, ls.MAX_ROWS + 1)]}}})
        for _ in range(300):
            clock.t += ls.MIN_GAP_S
            live.client._last_sent = None
            live.client.step()
        (kept,) = live.client._history.values()
        assert len(kept) == 1, f"an unchanged value is kept once, not per answer: {len(kept)}"
        assert len(kept[0][-1]["rows"]) == lw.PUBLIC_ROWS, "the history keeps only what the page can show"
        sim.clear_answers()
        for lap in range(300):
            clock.t += ls.MIN_GAP_S
            snap[0] = dict(SNAP, entries=[dict(e, laps=lap) for e in SNAP["entries"]])   # a new value every answer
            live.client._last_sent = None
            live.client.step()
        kept = max(len(h) for h in live.client._history.values())
        assert 100 < kept <= ls.HISTORY_S / ls.MIN_GAP_S + 1, f"{kept} answers kept per widget in a wake burst"


def t_the_answer_history_covers_the_longest_delay_and_stays_bounded():
    assert ls.HISTORY_S >= driver_race.MAX_DELAY_S + ls.HOLD_S, "the longest timing delay plus the hold"
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        live, _snap = _live(d, sim, clock=clock)
        for _ in range(400):
            clock.t += ls.TICK_S
            live.client.step()
        kept = max(len(h) for h in live.client._history.values())
        assert kept <= ls.HISTORY_S / ls.TICK_S + 1, f"{kept} answers kept per widget, older than HISTORY_S"


def _timing_data(live, delay_s, publisher=None):
    """(public timing body, timing host body) of a relay whose delayed snapshot carries `delay_s`."""
    import test_driver_routes as dr
    pub = dr._FakePublisher() if publisher else None
    srv, port, _lp, link, _hub = dr._timing_server(league_live=live, **(
        {"timing_publisher": pub.make, "funnel_host": dr.FUNNEL_HOST} if pub else {}))
    srv.race_svc.dashboard = lambda: {"v": 1, "mode": "race", "delay_s": delay_s, "entries": []}
    try:
        st, body, _h = dr._req(port, f"/console/timing/data?l={link}")
        assert st == 200, (st, body)
        return json.loads(body), json.loads(pub.fns["body_fn"]()[0]) if pub else None
    finally:
        srv.shutdown()


def t_the_public_timing_body_carries_the_delayed_block_and_the_host_the_same():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        clock = _Clock()
        env = _timing_env(d, "1")
        live, _snap = _live(d, sim, clock=clock, env_path=env)
        live.client.step()
        clock.t += 20
        body, hosted = _timing_data(live, 20, publisher=True)
        assert [w["id"] for w in body.get("league", [])] == ["match-score", "standings"], \
            f"the switch puts every event widget in the public body: {body}"
        assert hosted == body, "the timing host carries the same body, block included"
        body, _ = _timing_data(live, 30)
        assert "league" not in body, f"a value newer than the delay stays off the page: {body}"
        _timing_env(d, "0")
        os.utime(env, (50, 50))
        body, _ = _timing_data(live, 20)
        assert "league" not in body, "switch off: the body is the timing alone"


def _timing_bytes(league):
    """Bytes per answer of a 20-car public timing body carrying `league`, gzip plus headers."""
    import random
    import timing_feed
    rnd = random.Random(3)
    snap = {"v": 1, "mode": "race", "stint": "Stint 3", "remaining": 3600, "remaining_running": True,
            "delay_s": 30, "track_name": "Spa", "state": "racing", "lap": {"leader": 42, "count": 60},
            "entries": [{"pos": i + 1, "entry": f"Team {i} #{i + 1}", "number": str(i + 1),
                         "driver": f"Driver {rnd.randint(100, 999)}", "team": f"Team {i}", "laps": 42,
                         "gap_s": round(i * rnd.uniform(2, 4), 3), "interval_s": round(rnd.uniform(0.2, 4), 3),
                         "pit_stops": 2, "last_lap_ms": rnd.randint(97000, 99999), "best_ms": rnd.randint(96000, 97999),
                         "compound": "RM", "fuel_l": round(rnd.uniform(10, 90), 1), "brand": "bmw", "brand_name": "BMW"}
                        for i in range(20)]}
    body = dict(driver_race.public_view(snap), **({"league": league} if league else {}))
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return len(timing_feed.encode(raw)[2]) + timing_feed.HEADER_BYTES


def _block(answer, forms):
    values = ls.parse_widget_values(dict(answer, v=1), forms)
    return [{"id": wid, "form": forms[wid], "title": "T" * ls.MAX_TITLE,
             "value": lw.public_value(forms[wid], values[wid])} for wid in forms]


def t_the_upload_budget_still_holds_with_the_block():
    import random
    import test_timing_feed as tf
    rnd = random.Random(5)
    real = _block(league_sim.widget_values({"entries": [
        {"pos": i + 1, "team": f"Team {i % 2}", "driver": f"Driver {i}", "laps": 42} for i in range(20)]}),
        {"match-score": "score", "standings": "table"})
    letters = "abcdefghijklmnopqrstuvwxyz "
    worst = _block({"widgets": {f"w{i}": {"provisional": True, "value_title": "V" * ls.MAX_VALUE, "rows": [
        {"rank": ls.MAX_RANK - r, "name": "".join(rnd.choice(letters) for _ in range(ls.MAX_LABEL)),
         "value": str(rnd.randrange(10 ** (ls.MAX_VALUE - 1), 10 ** ls.MAX_VALUE))} for r in range(ls.MAX_ROWS)]}
        for i in range(ls.MAX_WIDGETS)}}, {f"w{i}": "table" for i in range(ls.MAX_WIDGETS)})
    assert len(worst) == ls.MAX_WIDGETS and all(len(w["value"]["rows"]) == lw.PUBLIC_ROWS for w in worst)
    for league, crowd in ((None, 2500), (real, 2000), (worst, 350)):
        nbytes = _timing_bytes(league)
        covered = int(1000 * 125 * tf.tf.MAX_INTERVAL_S / nbytes)
        assert crowd < covered, f"{nbytes} B per answer covers {covered} viewers, fewer than {crowd}"
        rates = tf._obeying_crowd(crowd, 400, nbytes=nbytes)
        assert max(rates) < 1000 * 1.3, f"{crowd} viewers at {nbytes} B reach {max(rates):.0f} kbit/s"


def t_a_failing_block_never_freezes_the_timing():
    class Broken:
        def public(self, _delay):
            raise RuntimeError("widget bug")
    body, _ = _timing_data(Broken(), 0)
    assert body["mode"] == "race" and "league" not in body, body


# The relay routes

def _serve(live, exporter=None):
    import test_console_gate as g
    srv = g._serve(league_live=live, league_exporter=exporter)
    return g, srv, srv.server_address[1]


def t_the_director_shows_and_hides_each_widget_with_a_get_route():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        live.client.step()
        g, srv, port = _serve(live)
        try:
            code, body = g._get(port, "/league/widgets")
            rows = json.loads(body)["widgets"]
            assert code == 200 and [r["shown"] for r in rows] == [False, False], rows
            code, body = g._get(port, "/league-widgets/data")
            assert code == 200 and json.loads(body)["widgets"] == []
            code, body = g._get(port, "/league/widget/1/toggle")
            assert code == 200 and json.loads(body) == {"ok": True, "id": "match-score", "action": "toggle"}, body
            assert [w["id"] for w in json.loads(g._get(port, "/league-widgets/data")[1])["widgets"]] == ["match-score"]
            assert g._get(port, "/league/widget/match-score/hide")[0] == 200
            assert json.loads(g._get(port, "/league-widgets/data")[1])["widgets"] == []
            code, body = g._get(port, "/league/widget/8/show")
            assert code == 400 and "lists 2" in json.loads(body)["error"], body
            assert g._get(port, "/league/widget/match-score/explode")[0] == 400
            code, body = g._get(port, "/league-widgets.js")
            assert code == 200 and "RacecastLeague" in body
        finally:
            srv.shutdown()


def t_the_widget_routes_are_director_only_under_console():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        live, _snap = _live(d, sim)
        g, srv, port = _serve(live)
        try:
            assert g._get(port, "/console/league/widget/1/show", g._tok("alice"))[0] == 403, "a commentator"
            assert g._get(port, "/console/league/widget/1/show", g._tok("dave"))[0] == 403, "Race Control"
            assert g._get(port, "/console/league/widget/1/show", g._tok("bob"))[0] == 200, "the director"
            assert g._get(port, "/console/league/widgets", g._tok("bob"))[0] == 200
            for path in ("/console/league-widgets/data", "/console/league-widgets.js"):
                assert g._get(port, path, g._tok("bob"))[0] == 404, f"{path}: the overlay feed is never funnelled"
            live.set("1", "hide")
            cross = {"Sec-Fetch-Site": "cross-site"}
            for path, token in (("/league/widget/1/show", None), ("/console/league/widget/1/show", g._tok("bob"))):
                assert g._get(port, path, token, headers=cross)[0] == 403, f"{path}: a foreign page cannot switch"
            assert not any(r["shown"] for r in live.director()["widgets"]), "the refused requests changed nothing"
        finally:
            srv.shutdown()


def t_without_the_live_sender_the_routes_say_so():
    import test_console_gate as g
    srv = g._serve()
    port = srv.server_address[1]
    try:
        assert g._get(port, "/league/widgets")[0] == 404
        assert g._get(port, "/league/widget/1/show")[0] == 404
        assert json.loads(g._get(port, "/league-widgets/data")[1]) == {"ok": True, "widgets": []}
    finally:
        srv.shutdown()


def t_the_dashboard_tick_hands_the_newest_snapshot_to_the_sender():
    import test_console_gate as g

    class _Relay:
        _warn_tick_error = g.m.Relay._warn_tick_error
        _tick_ok = g.m.Relay._tick_ok

    class _Svc:
        ticks = 0

        def tick(self):
            self.ticks += 1

        def newest(self):
            return {"n": self.ticks}

        def race_state(self):
            return None

    class _Live:
        seen = []

        def observe(self, snap):
            self.seen.append(snap["n"])
            if snap["n"] == 2:
                raise OSError("disk full")

    class _Stop:
        n = 3

        def wait(self, _s):
            self.n -= 1
            return self.n < 0
    svc, live = _Svc(), _Live()
    g.m.dashboard_ticks(_Relay(), svc, _Stop(), 0, None, live)
    assert live.seen == [1, 2, 3], f"every tick reaches the sender and its failure never stops the tick: {live.seen}"


def t_a_profile_edit_reloads_the_sender_settings():
    with tempfile.TemporaryDirectory() as d:
        env = os.path.join(d, "profile.env")
        with open(env, "w", encoding="utf-8") as fh:
            fh.write("NAME=x\n")
        live, snap = _live(d, None, env_path=env)
        live.client._reload = False
        live.observe(snap[0])
        assert not live.client._reload, "an unchanged profile.env reloads nothing"
        os.utime(env, ns=(1, 1))
        live.observe(snap[0])
        assert live.client._reload, "an edited profile.env applies without a relay restart"


def t_companion_toggles_the_first_two_widgets_with_a_get_route():
    with open(os.path.join(ROOT, "src", "companion", "racecast-buttons.companionconfig"), encoding="utf-8") as fh:
        page = json.load(fh)["pages"]["3"]["controls"]["1"]
    for col, n in (("6", 1), ("7", 2)):
        (action,) = page[col]["steps"]["0"]["action_sets"]["down"]
        assert action["definitionId"] == "get", "a Companion button sends no body: it must be a GET"
        assert action["options"]["url"]["value"] == f"http://127.0.0.1:8088/league/widget/{n}/toggle", action
        assert page[col]["style"]["text"] == f"LEAGUE\nWIDGET {n}"


# The overlay renderer

_DOM = r"""
const fs = require('fs'), vm = require('vm');
const made = [];
function el(tag) {
  const e = {tagName: tag, children: [], _text: '', className: '', hidden: false, attrs: {},
    classList: {contains(c) { return e.className.split(' ').includes(c); }},
    getAttribute(a) { return e.attrs[a]; },
    querySelector(sel) { const c = sel.slice(1); const find = n => n.className === c ? n
      : n.children.map(find).find(Boolean); return e.children.map(find).find(Boolean) || null; },
    append(...k) { e.children.push(...k); }, appendChild(k) { e.children.push(k); return k; },
    replaceChildren(...k) { e.children = k; },
    set textContent(v) { e._text = String(v); e.children = []; }, get textContent() { return e._text; }};
  for (const bad of ['innerHTML', 'outerHTML'])
    Object.defineProperty(e, bad, {set() { throw new Error(bad + ' is never set'); }});
  e.insertAdjacentHTML = () => { throw new Error('insertAdjacentHTML is never used'); };
  made.push(e);
  return e;
}
global.window = global;
global.performance = {now: () => 0};
// A slot of `height` px: 40 px of title, 20 of table head, 30 per table line.
const lines = e => e.children.reduce((n, k) => n + (['lw-rank', 'lw-more'].includes(k.className) ? 30
  : k.className === 'lw-head' ? 20 : 0) + lines(k), 0);
const slots = JSON.parse(process.argv[3]).map(s => { const e = el('div');
  e.className = 'league-widget lw-form-' + s.form; e.attrs['data-league-widget'] = s.id; e.hidden = true;
  if (s.height) { e.clientHeight = s.height; Object.defineProperty(e, 'scrollHeight', {get: () => 40 + lines(e)}); }
  return e; });
made.length = 0;
global.document = {currentScript: null, readyState: 'complete', createElement: el,
  querySelectorAll: () => slots};
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));
function walk(e, tags, texts) { tags.push(e.tagName); texts.push(e._text); e.children.forEach(k => walk(k, tags, texts)); }
const data = JSON.parse(process.argv[4]);
RacecastLeague.render(document, data, 0, 0);
const out = {hidden: slots.map(s => s.hidden), tags: [], texts: []};
slots.filter(s => !s.hidden).forEach(s => s.children.forEach(k => walk(k, out.tags, out.texts)));
RacecastLeague.expire(document, 31000);
out.expired = slots.map(s => s.hidden);
console.log(JSON.stringify(out));
"""


def _render(slots, data):
    node = shutil.which("node")
    if not node:
        print("  (node not installed, renderer check skipped)")
        return None
    res = subprocess.run([node, "-", RENDER_JS, json.dumps(slots), json.dumps(data)], input=_DOM,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def t_the_renderer_draws_score_and_table_and_hides_the_rest():
    data = {"widgets": [
        {"id": "s", "form": "score", "title": "Match", "holdFor": 30,
         "value": {"provisional": True, "values": [{"label": "A", "value": "3"}]}},
        {"id": "t", "form": "table", "title": "Champ", "holdFor": None,
         "value": {"value_title": "Pts", "rows": [{"rank": 1, "name": "Ann", "value": "25"}]}},
        {"id": "x", "form": "table", "title": "Form mismatch", "holdFor": 30, "value": {}}]}
    out = _render([{"id": "s", "form": "score"}, {"id": "t", "form": "table"}, {"id": "x", "form": "score"},
                   {"id": "gone", "form": "score"}], data)
    if out is None:
        return
    assert out["hidden"] == [False, False, True, True], out
    assert out["texts"] == ["Match", "provisional", "", "", "A", "3",
                            "Champ", "", "Pts", "1", "Ann", "25"], out["texts"]
    assert out["expired"] == [True, False, True, True], "a held value hides at its deadline, a sample never"


def t_a_table_keeps_only_the_whole_rows_that_fit_and_counts_the_rest():
    rows = [{"rank": i, "name": f"Driver {i}", "value": str(30 - i)} for i in range(1, 21)]
    data = {"widgets": [{"id": "t", "form": "table", "title": "Champ", "holdFor": 30,
                         "value": {"value_title": "Pts", "rows": rows}},
                        {"id": "u", "form": "table", "title": "Short", "holdFor": 30,
                         "value": {"rows": rows[:2]}}]}
    out = _render([{"id": "t", "form": "table", "height": 200}, {"id": "u", "form": "table", "height": 200}], data)
    if out is None:
        return
    first = out["texts"][:out["texts"].index("Short")]
    # 200 px: 40 title + 20 head + 3 rows + the "+17 more" line = 190
    assert first == ["Champ", "", "Pts", "1", "Driver 1", "29", "2", "Driver 2", "28", "3", "Driver 3", "27",
                     "+17 more"], first
    assert out["texts"][out["texts"].index("Short"):] == ["Short", "", "1", "Driver 1", "29", "2", "Driver 2", "28"], \
        "a table that fits keeps every row and no count line"


def t_the_renderer_never_parses_service_text_as_markup():
    with open(RENDER_JS, encoding="utf-8") as fh:
        js = fh.read()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js, f"{sink} would parse service text"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

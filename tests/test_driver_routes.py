#!/usr/bin/env python3
"""The driver telemetry routes under /console/drive and on the tailnet (#1111).
Run: python3 tests/test_driver_routes.py"""
import gzip, json, os, struct, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import driver_ingest  # noqa: E402
import driver_race  # noqa: E402
import driver_invite  # noqa: E402
import timing_link  # noqa: E402
import gt7_crypto  # noqa: E402
import telemetry_probe as tp  # noqa: E402
import test_console_gate as g  # noqa: E402
from test_gt7_telemetry import _packet  # noqa: E402

ENTRIES = ["Team A #1", "Team B #2"]


class _Hud:
    def roster_names(self):
        return list(ENTRIES)


def _wire(pid):
    plain = bytearray(_packet(lap=1))
    struct.pack_into("<i", plain, 0x70, pid)
    return gt7_crypto.encrypt(bytes(plain), "A", iv=pid)


def _req(port, path, data=None, headers=None, method=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def _req_raw(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _timing_server(**extra):
    d = tempfile.mkdtemp()
    link_path = os.path.join(d, "timing-link.json")
    srv, port, _path, hub = _serve(timing_link_path=link_path,
                                   timing_page_path=os.path.join(ROOT, "src", "console", "timing.html"),
                                   **extra)
    return srv, port, link_path, timing_link.create(link_path)["id"], hub


def _serve(solo=True, segments=(), clock=None, **extra):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "driver-invite.json")
    from test_race_distance import TRACE
    race = driver_race.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "t"},
                               trace_for=lambda _i: TRACE, multi_lobby=lambda: not solo)
    hub = driver_ingest.Hub(multi_lobby=lambda: not solo, on_sample=race.update,
                            **({"clock": clock} if clock else {}))
    srv = g._serve(solo=solo, hud_source=_Hud(), driver_hub=hub, driver_invite_path=path,
                   drive_page_path=os.path.join(ROOT, "src", "console", "drive.html"),
                   sender_script_path=os.path.join(ROOT, "src", "relay", "racecast-sender.py"),
                   driver_segments=lambda: list(segments), driver_race_svc=race,
                   driver_desk_path=os.path.join(ROOT, "src", "console", "driver-desk.js"), **extra)
    srv.race_svc = race
    return srv, srv.server_address[1], path, hub


def t_the_dashboard_page_and_its_override_are_served_on_the_tailnet():
    od = tempfile.mkdtemp()
    with open(os.path.join(od, "drivers.css"), "w", encoding="utf-8") as fh:
        fh.write("#drivers-tower{left:90px}")
    srv = g._serve(drivers_path=os.path.join(ROOT, "src", "obs", "drivers.html"), overlay_dir=od)
    port = srv.server_address[1]
    try:
        st, text, _h = _req(port, "/drivers")
        assert st == 200 and 'id="drivers-tower"' in text and "/drivers/override.css" in text, (st, text[:80])
        st, text, h = _req(port, "/drivers/override.css")
        assert st == 200 and text == "#drivers-tower{left:90px}" and h["Content-Type"].startswith("text/css"), text
        assert _req(port, "/console/drivers")[0] in (401, 403), "the overlay page is not funnelled"
    finally:
        srv.shutdown()


def t_the_public_timing_link_reaches_timing_data_and_nothing_else():
    d = tempfile.mkdtemp()
    link_path = os.path.join(d, "timing-link.json")
    from test_race_distance import TRACE
    race = driver_race.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "t"},
                               trace_for=lambda _i: TRACE, multi_lobby=lambda: False)
    srv = g._serve(driver_race_svc=race, timing_link_path=link_path,
                   timing_page_path=os.path.join(ROOT, "src", "console", "timing.html"))
    port = srv.server_address[1]
    try:
        assert _req(port, "/console/timing?l=x")[0] == 403, "no event, no timing link"
        link = timing_link.create(link_path)["id"]
        invite_like = driver_invite.create(os.path.join(d, "driver-invite.json"))["id"]
        assert _req(port, f"/console/timing?l={invite_like}")[0] == 403, "the drivers' invite is no timing link"
        st, text, h = _req(port, f"/console/timing?l={link}")
        assert st == 200 and "Live timing" in text and "Set-Cookie" not in h, (st, h)
        race.tick()
        st, text, _h = _req(port, f"/console/timing/data?l={link}")
        data = json.loads(text)
        assert st == 200 and data["v"] == 1 and "entries" in data, text[:200]
        for leak in ("conn", "https://", "visible", '"x"'):
            assert leak not in text, f"{leak} must not reach the public page"
        assert _req(port, f"/console/timing/data?l={link}", b"{}", {"Content-Type": "application/json"})[0] == 404
        assert _req(port, f"/console/race-control/drivers/timing-link?t={g._tok('dave')}")[0] == 200
        assert _req(port, "/console/drivers/race?l=" + link)[0] in (401, 403), "the link opens nothing else"
        timing_link.clear(link_path)
        assert _req(port, f"/console/timing?l={link}")[0] == 403, "event stop ends the link"
    finally:
        srv.shutdown()


def t_cross_site_clicks_open_the_public_pages_and_timing_failures_spare_the_drivers():
    link_path = os.path.join(tempfile.mkdtemp(), "timing-link.json")
    srv, port, path, _hub = _serve(timing_link_path=link_path,
                                   timing_page_path=os.path.join(ROOT, "src", "console", "timing.html"))
    cross = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    try:
        invite = driver_invite.create(path)["id"]
        link = timing_link.create(link_path)["id"]
        st, text, _h = _req(port, f"/console/drive?i={invite}", headers=cross)
        assert st == 200 and "Cross-origin" not in text, "an invite clicked in Discord or webmail opens"
        assert _req(port, f"/console/timing?l={link}", headers=cross)[0] == 200
        assert _req(port, "/console/drive/register", b"{}", dict(cross, **{"Content-Type": "application/json"}))[0] == 403, \
            "only the page GET is exempt"
        codes = [_req(port, "/console/timing?l=wrong")[0] for _ in range(130)]
        assert 429 in codes, "wrong timing links are capped"
        st, _t, _h = _req(port, "/console/drive/ingest", tp.encode_batch([_wire(1)]), {"X-Driver-Code": "D1:x.y"})
        assert st == 403, f"a scan of the timing link never answers a driver with 429: {st}"
    finally:
        srv.shutdown()


def _register(port, invite, **body):
    return _req(port, f"/console/drive/register?i={invite}", json.dumps(body).encode(),
                {"Content-Type": "application/json"})


def t_invite_page_data_register_and_ingest_in_a_single_lobby():
    srv, port, path, hub = _serve()
    try:
        assert _req(port, "/console/drive/data?i=x")[0] == 403, "no event, no invite"
        invite = driver_invite.create(path)["id"]
        code, text, headers = _req(port, f"/console/drive?i={invite}")
        assert code == 200 and "<html" in text.lower() and "Set-Cookie" not in headers, (code, headers)
        code, text, _h = _req(port, f"/console/drive/data?i={invite}")
        data = json.loads(text)
        assert data == {"v": 1, "multi_lobby": False, "roster": True, "entries": ENTRIES,
                        "segments": [], "rate_hz": 20}, data
        assert "https://" not in text, "the invite data carries no stream URLs"
        code, text, _h = _req(port, f"/console/drive/sender.py?i={invite}")
        assert code == 200 and "racecast relay" in text, code
        code, text, _h = _register(port, invite, entry="Team A #1", name="Max")
        issued = json.loads(text)["code"]
        assert code == 200 and issued.startswith("D1:"), text
        for bad in ({"entry": "Nobody", "name": "Max"}, {"entry": "Team A #1", "name": "Car #7"}):
            assert _register(port, invite, **bad)[0] == 400, bad
        batch = tp.encode_batch([_wire(1), _wire(2)])
        code, text, _h = _req(port, "/console/drive/ingest", batch, {"X-Driver-Code": issued})
        assert code == 200 and json.loads(text) == {"ok": True, "v": 1, "rate_hz": 20, "accepted": 2}, text
        other = json.loads(_register(port, invite, entry="Team A #1", name="Ann")[1])["code"]
        code, text, _h = _req(port, "/console/drive/ingest", batch, {"X-Driver-Code": other})
        assert code == 409 and "already has a sender" in text, (code, text)
        code, text, _h = _req(port, "/drivers/data")
        conns = json.loads(text)["connections"]
        assert [(c["entry"], c["name"], c["packets"]["received"]) for c in conns] == [("Team A #1", "Max", 2)], conns
        ENTRIES.append("Team C #3")
        try:
            late = json.loads(_register(port, invite, entry="Team C #3", name="Lea")[1])["code"]
        finally:
            ENTRIES.remove("Team C #3")                               # the league dropped the entry
        code, text, _h = _req(port, "/console/drive/ingest", batch, {"X-Driver-Code": late})
        assert code == 403 and "no longer in the league" in text, (code, text)
        driver_invite.create(path)                                    # the producer regenerates
        code, text, _h = _req(port, "/console/drive/ingest", batch, {"X-Driver-Code": issued})
        assert code == 403 and "register again" in text, (code, text)
        assert _req(port, f"/console/drive/data?i={invite}")[0] == 403, "the old link is dead"
    finally:
        srv.shutdown()


def t_endurance_asks_for_the_stint_and_allows_two_per_team():
    srv, port, path, _hub = _serve(solo=False, segments=["Stint 1", "Stint 2", "Q1"])
    try:
        invite = driver_invite.create(path)["id"]
        data = json.loads(_req(port, f"/console/drive/data?i={invite}")[1])
        assert data["multi_lobby"] is True and data["segments"] == ["Stint 1", "Stint 2", "Q1"], data
        assert _register(port, invite, entry="Team A #1", name="Max")[0] == 400, "endurance needs the stint"
        codes = [json.loads(_register(port, invite, entry="Team A #1", name=n, segment=s)[1])["code"]
                 for n, s in (("Max", "Stint 1"), ("Ann", "Stint 2"), ("Lea", "Q1"))]
        batch = tp.encode_batch([_wire(1)])
        replies = [_req(port, "/console/drive/ingest", batch, {"X-Driver-Code": c})[0] for c in codes]
        assert replies == [200, 200, 409], replies
    finally:
        srv.shutdown()


def t_endurance_without_stint_labels_registers_by_entry():
    srv, port, path, _hub = _serve(solo=False, segments=[])
    try:
        invite = driver_invite.create(path)["id"]
        code, text, _h = _register(port, invite, entry="Team A #1", name="Max", segment="whatever")
        assert code == 200, (code, text)
        reg = driver_invite.verify(driver_invite.current(path), json.loads(text)["code"])
        assert reg["segment"] is None, reg
    finally:
        srv.shutdown()


def t_driver_credentials_reach_nothing_else():
    srv, port, path, hub = _serve()
    try:
        invite = driver_invite.create(path)["id"]
        issued = json.loads(_register(port, invite, entry="Team A #1", name="Max")[1])["code"]
        for route in ("/console", "/console/cockpit", "/console/status", "/console/drivers/data"):
            code, _t, _h = _req(port, f"{route}?i={invite}", headers={"X-Driver-Code": issued})
            assert code in (401, 403, 404), (route, code)
        code, _t, _h = _req(port, "/console/drive/ingest", tp.encode_batch([_wire(1)]),
                            {"X-Driver-Code": "D1:forged.sig"})
        assert code == 403, code
        assert _req(port, "/console/drive/ingest", b"RCT1", {"X-Driver-Code": issued}, method="GET")[0] in (403, 405)
    finally:
        srv.shutdown()


def t_the_producer_disconnects_a_sender_on_the_tailnet():
    srv, port, path, hub = _serve()
    try:
        invite = driver_invite.create(path)["id"]
        issued = json.loads(_register(port, invite, entry="Team A #1", name="Max")[1])["code"]
        _req(port, "/console/drive/ingest", tp.encode_batch([_wire(1)]), {"X-Driver-Code": issued})
        conn = hub.connections()[0]["conn"]
        code, text, _h = _req(port, "/drivers/disconnect", json.dumps({"conn": conn}).encode(),
                              {"Content-Type": "application/json"})
        assert code == 200 and json.loads(text) == {"ok": True}, (code, text)
        code, text, _h = _req(port, "/console/drive/ingest", tp.encode_batch([_wire(2)]), {"X-Driver-Code": issued})
        assert code == 410 and "disconnected" in text, (code, text)
    finally:
        srv.shutdown()


def _wire_at(pid, along, lap):
    from test_race_distance import _pos
    x, z = _pos(along)
    plain = bytearray(_packet(lap=lap, speed_mps=50.0, pos=(x, 0.0, z)))
    struct.pack_into("<i", plain, 0x70, pid)
    return gt7_crypto.encrypt(bytes(plain), "A", iv=pid)


def t_ingested_telemetry_becomes_the_race_order_on_the_tailnet():
    sim = [100.0]                    # the relay's clock follows the simulated packets, not the host
    srv, port, path, _hub = _serve(clock=lambda: sim[0])
    try:
        invite = driver_invite.create(path)["id"]
        _req(port, "/drivers/race/settings", json.dumps({"formation": False}).encode(),
             {"Content-Type": "application/json"})
        codes = {e: json.loads(_register(port, invite, entry=e, name="N")[1])["code"] for e in ENTRIES}
        for i in range(0, 600, 6):                      # ten seconds at 60 Hz, sampled at 10 Hz
            sim[0] = 100.0 + i / 60
            for entry, behind in ((ENTRIES[0], 20.0), (ENTRIES[1], 60.0)):
                along = 50.0 * i / 60 - behind               # from the grid across the line
                _req(port, "/console/drive/ingest",
                     tp.encode_batch([_wire_at(i + 6, along, 1 if along >= 0 else 0)]),
                     {"X-Driver-Code": codes[entry]})
        code, text, _h = _req(port, "/drivers/race")
        race = json.loads(text)
        assert code == 200 and race["state"] == "racing" and race["track"] == "t", race
        assert [r["entry"] for r in race["entries"]] == ENTRIES, race["entries"]
        assert race["settings"]["formation"] is False
        code, text, _h = _req(port, "/drivers/race/settings", json.dumps({"lap_count": 0}).encode(),
                              {"Content-Type": "application/json"})
        assert code == 400 and "lap_count" in text, (code, text)
        hdr = {"Content-Type": "application/json"}
        code, text, _h = _req(port, "/drivers/race/correct", json.dumps(
            {"entry": ENTRIES[1], "metres": 200, "reason": "missed lap"}).encode(), hdr)
        race = json.loads(text)["race"]
        assert code == 200 and [r["entry"] for r in race["entries"]] == ENTRIES[::-1], race["entries"]
        assert race["last_action"]["who"] == "tailnet" and race["last_action"]["action"] == "correct"
        code, text, _h = _req(port, "/drivers/race/correct", json.dumps(
            {"entry": ENTRIES[1], "metres": 200}).encode(), hdr)
        assert code == 400 and "reason" in text, (code, text)
        code, text, _h = _req(port, "/drivers/race/red-flag", json.dumps({"mode": "neutralise"}).encode(), hdr)
        assert code == 200 and json.loads(text)["race"]["red_flag"]["mode"] == "neutralise", text
        code, text, _h = _req(port, "/drivers/race/release", b"{}", hdr)
        assert code == 200 and json.loads(text)["race"]["red_flag"] is None
        code, text, _h = _req(port, "/drivers/race/change", json.dumps(
            {"entry": ENTRIES[0], "conn": "nobody"}).encode(), hdr)
        assert code == 400 and "sender" in text, (code, text)
        for action, state in (("final-lap", "final_lap"), ("finish", "finished"), ("reset", "waiting")):
            code, text, _h = _req(port, f"/drivers/race/{action}", b"{}", {"Content-Type": "application/json"})
            assert code == 200 and json.loads(text)["state"] == state, (action, code, text)
        assert _req(port, "/console/drivers/race")[0] in (401, 403, 404), "the race controls stay on the tailnet"
    finally:
        srv.shutdown()


def t_a_stint_switch_opens_the_change_window_and_the_last_row_brings_its_laps():
    import threading, time

    class Source:
        def get_rows(self):
            return [("u", "A", "S1", 2), ("u", "B", "S2", 3), ("u", "C", "S3", 4)]

        def final_laps(self):
            return 24

    class Relay:
        solo, mode, race_source, idx = False, "race", Source(), 0
        _warn_tick_error, _tick_ok = g.m.Relay._warn_tick_error, g.m.Relay._tick_ok

        def on_air_row_idx(self):
            return self.idx

    calls, relay, stop = [], Relay(), threading.Event()

    class Svc:
        def stint_switch(self, final_laps=None, window=True):
            calls.append(final_laps if window else ("start", final_laps))
    worker = threading.Thread(target=g.m.stint_switch_watch,
                              args=(relay, Svc(), stop, lambda: 9, 0.01), daemon=True)
    seen = threading.Event()
    real = relay.on_air_row_idx
    relay.on_air_row_idx = lambda: (seen.set(), real())[1]
    worker.start()
    try:
        assert seen.wait(2), "the watcher reads the starting row first"
        for idx in (1, 2):
            relay.idx = idx
            deadline = time.monotonic() + 2
            while len(calls) < idx and time.monotonic() < deadline:
                time.sleep(0.01)
    finally:
        stop.set()
        worker.join(2)
    assert calls == [None, 24], f"switches to row 2 and to the last row: {calls}"
    calls.clear()
    relay.idx, stop = 2, threading.Event()
    worker = threading.Thread(target=g.m.stint_switch_watch,
                              args=(relay, Svc(), stop, lambda: 9, 0.01), daemon=True)
    worker.start()
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    worker.join(2)
    assert calls == [("start", 24)], f"a relay start on the last row passes its laps once: {calls}"


def t_both_desks_act_with_their_identity():
    import json as _json
    srv, port, path, hub = _serve()
    hdr = {"Content-Type": "application/json"}
    try:
        invite = driver_invite.create(path)["id"]
        code = _json.loads(_register(port, invite, entry=ENTRIES[0], name="Max")[1])["code"]
        _req(port, "/console/drive/ingest", tp.encode_batch([_wire_at(6, -20.0, 0)]),
             {"X-Driver-Code": code})
        conn = hub.connections()[0]["conn"]
        rc, director = g._tok("dave"), g._tok("bob")
        st, text, _h = _req(port, f"/console/race-control/drivers/race/correct?t={rc}", _json.dumps(
            {"entry": ENTRIES[0], "metres": 50, "reason": "cut"}).encode(), hdr)
        assert st == 200 and _json.loads(text)["race"]["last_action"]["who"] == "race_control:dave", text
        st, text, _h = _req(port, f"/console/drivers/race/final-lap?t={director}", b"{}", hdr)
        assert st == 200 and srv.race_svc.standings()["last_action"]["who"] == "director:bob", text
        assert _req(port, f"/console/drivers/race?t={rc}")[0] == 403, "the panel route is director-tier"
        on_air = _json.dumps({"visible": True}).encode()
        st, text, _h = _req(port, f"/console/race-control/drivers/race/settings?t={rc}", on_air, hdr)
        assert st == 403 and not driver_race.read_settings(srv.race_svc._path)["visible"], \
            f"race control never puts the dashboard on air: {st} {text}"
        st, _t, _h = _req(port, f"/console/race-control/drivers/race/settings?t={rc}",
                          _json.dumps({"delay_s": 10}).encode(), hdr)
        assert st == 200, "race control keeps the other race settings"
        st, text, _h = _req(port, f"/console/race-control/drivers/race/quali?t={rc}",
                            _json.dumps({"entry": ENTRIES[0], "ms": None}).encode(), hdr)
        assert st == 200 and _json.loads(text)["race"]["quali_overrides"] == {ENTRIES[0]: None}, text
        st, text, _h = _req(port, f"/console/race-control/drivers/race/compound?t={rc}",
                            _json.dumps({"entry": ENTRIES[0], "code": "RS"}).encode(), hdr)
        tyre = _json.loads(text)["race"]["compounds"][ENTRIES[0]]
        assert st == 200 and tyre["code"] == "RS" and tyre["who"] == "race_control:dave", text
        st, _t, _h = _req(port, f"/console/race-control/drivers/race/compound?t={rc}",
                          _json.dumps({"entry": "Invented Team", "code": "RS"}).encode(), hdr)
        assert st == 400, "only roster entries carry a compound"
        st, text, _h = _req(port, f"/console/race-control/drivers/race/quali?t={rc}",
                            _json.dumps({"entry": "Invented Team", "ms": 1000}).encode(), hdr)
        assert st == 400 and "unknown entry" in text, "only roster entries get a qualifying time"
        st, _t, _h = _req(port, f"/console/drivers/race/settings?t={director}", on_air, hdr)
        assert st == 200 and driver_race.read_settings(srv.race_svc._path)["visible"], "the director does"
        st, text, _h = _req(port, f"/console/race-control/drivers/readiness?t={rc}")
        rows = _json.loads(text)["rows"]
        assert st == 200 and [r["entry"] for r in rows if r["ready"]] == [ENTRIES[0]], rows
        st, text, _h = _req(port, f"/console/race-control/drivers/invite?t={rc}")
        assert st == 200 and _json.loads(text)["id"] == invite, text
        st, text, _h = _req(port, f"/console/race-control/drivers/reassign?t={rc}",
                            _json.dumps({"conn": conn, "entry": ENTRIES[1]}).encode(), hdr)
        assert st == 200 and hub.connections()[0]["entry"] == ENTRIES[1], text
        st, text, _h = _req(port, f"/console/race-control/drivers/reassign?t={rc}",
                            _json.dumps({"conn": conn, "entry": "Nobody #0"}).encode(), hdr)
        assert st == 400 and "entry" in text, (st, text)
        st, text, _h = _req(port, f"/console/race-control/red-flag?t={rc}",
                            _json.dumps({"on": False}).encode(), hdr)
        body = _json.loads(text)
        assert st == 200 and body["banner"] == "not raised here" and body["note"] == "not raised from this desk", \
            "a cover or banner this desk did not raise is never lowered from it"
        time.sleep(g.m.RED_FLAG_MIN_S + 0.1)
        st, text, _h = _req(port, f"/console/race-control/red-flag?t={rc}", b"{}", hdr)
        body = _json.loads(text)
        assert st == 200 and body["on"] and body["race"]["red_flag"] is not None, text
        assert "127.0.0.1" not in text and "4455" not in text, "no OBS address reaches the desk"
        assert _req(port, f"/console/race-control/red-flag?t={rc}", b"{}", hdr)[0] == 429, \
            "one red flag change per person in 2 s"
        assert _req(port, f"/console/race-control/red-flag?t={director}", b"{}", hdr)[0] == 403
        actions = [r["action"] for r in _audit_of(srv)]
        assert actions == ["correct", "final_lap", "settings", "quali", "compound", "settings",
            "reassign", "red_flag_cover_down", "red_flag"], actions
        carol = g._tok("carol")                                   # producer: every role
        assert _req(port, f"/console/race-control/drivers/data?t={carol}")[0] == 403
        assert _req(port, f"/console/drivers/data?t={carol}")[0] == 404
        assert _req(port, f"/console/drivers/race/reset?t={carol}", b"{}", hdr)[0] == 404, \
            "the race reset stays on the tailnet"
        srv.race_svc.tick()
        alice = g._tok("alice")                                   # a commentator
        st, text, _h = _req(port, f"/console/drivers/dashboard?t={alice}")
        dash = _json.loads(text)
        assert st == 200 and dash["v"] == 1 and "conn" not in text, (st, text[:200])
        st, text, _h = _req(port, f"/console/drivers/line?t={alice}")
        assert st == 200 and len(_json.loads(text)["points"]) > 100, text[:120]
        assert _req(port, f"/console/drivers/readiness?t={alice}")[0] == 403, "controls stay crew-only"
        st, text, h = _req(port, "/console/driver-desk.js")
        assert st == 200 and "javascript" in h.get("Content-Type", ""), (st, h)
    finally:
        srv.shutdown()


class _CoverObs:
    """OBS with one Standby Cover: reads and switches it."""
    def __init__(self, up):
        self.up = up

    def graphic_scene(self, solo):
        return "Stint"

    def read_obs_state(self, items, _inputs):
        return {"scene": "Stint", "sources": [{"enabled": self.up}]}, None

    def set_scene_item_enabled(self, scene, source, on):
        if source == "Standby Cover":
            self.up = bool(on)
        return True, None


def t_race_control_lowers_only_a_cover_it_raised():
    obs, hdr = _CoverObs(up=True), {"Content-Type": "application/json"}
    orig_obs, orig_min, g.m._obs_ws, g.m.RED_FLAG_MIN_S = g.m._obs_ws, g.m.RED_FLAG_MIN_S, obs, 0.0
    srv, port, _path, _hub = _serve()
    rc = g._tok("dave")
    flag = lambda on: _req(port, f"/console/race-control/red-flag?t={rc}",
                           json.dumps({"on": on}).encode(), hdr)
    try:
        assert flag(True)[0] == 200 and flag(False)[0] == 200
        assert obs.up, "an auto-cover that was already up stays up"
        obs.up = False
        flag(True)
        assert obs.up, "a raise from the desk shows the cover"
        st, _t, _h = _req(port, "/obs/source", json.dumps(
            {"scene": "Stint", "source": "Standby Cover", "on": True}).encode(), hdr)
        assert st == 200
        flag(False)
        assert obs.up, "once the director switched the cover, the desk no longer owns it"
        obs.up = False
        flag(True)
        assert _req(port, "/obs/graphic/show/Standby%20Cover")[0] == 200
        flag(False)
        assert obs.up, "a Companion switch of the cover ends the desk's ownership too"
        obs.up = False
        flag(True)
        flag(False)
        assert not obs.up, "the desk lowers the cover it raised"
        flag(True)
        flag(True)
        flag(False)
        assert not obs.up, "a second raise over the desk's own cover keeps the desk's claim"
    finally:
        g.m._obs_ws, g.m.RED_FLAG_MIN_S = orig_obs, orig_min
        srv.shutdown()


def t_a_failing_dashboard_tick_warns_once_per_distinct_error():
    import logging

    class _Relay:
        _warn_tick_error = g.m.Relay._warn_tick_error
        _tick_ok = g.m.Relay._tick_ok

    class _Svc:
        results = [ValueError("a")] * 4 + [None] + [ValueError("a")] * 3 + [KeyError("b")]

        def tick(self):
            r = self.results.pop(0)
            if r is not None:
                raise r

    class _Stop:
        n = len(_Svc.results)

        def wait(self, _s):
            self.n -= 1
            return self.n < 0
    warnings = []
    handler = logging.Handler()
    handler.emit = lambda rec: warnings.append(rec.getMessage()) if rec.levelno >= logging.WARNING else None
    g.m.LOG.addHandler(handler)
    try:
        g.m.dashboard_ticks(_Relay(), _Svc(), _Stop(), 0)
    finally:
        g.m.LOG.removeHandler(handler)
    assert len(warnings) == 3, f"one warning per distinct error and again after recovery: {warnings}"


def t_timing_data_is_gzipped_revalidated_and_paced():
    srv, port, _lp, link, _hub = _timing_server()
    path = f"/console/timing/data?l={link}"
    try:
        st, body, h = _req_raw(port, path, {"Accept-Encoding": "gzip"})
        assert st == 200 and h.get("Content-Encoding") == "gzip", (st, h)
        assert json.loads(gzip.decompress(body))["v"] == 1
        etag = h.get("ETag")
        assert etag and float(h["X-Poll-After"]) >= 2.0, h
        st, body, h = _req_raw(port, path, {"Accept-Encoding": "gzip", "If-None-Match": etag})
        assert st == 304 and body == b"" and float(h["X-Poll-After"]) >= 2.0, (st, h)
        st, body, h = _req_raw(port, path)
        assert st == 200 and "Content-Encoding" not in h and json.loads(body)["v"] == 1, h
        assert h.get("ETag") not in (None, etag), "a plain body has its own strong ETag"
    finally:
        srv.shutdown()


def t_no_viewer_is_turned_away_by_load():
    srv, port, _lp, link, _hub = _timing_server()
    try:
        codes = {_req_raw(port, f"/console/timing/data?l={link}")[0] for _ in range(60)}
        assert codes <= {200, 304}, f"load stretches the interval, it never rejects: {codes}"
    finally:
        srv.shutdown()


def t_ended_link_beats_a_held_etag():
    srv, port, link_path, link, _hub = _timing_server()
    path = f"/console/timing/data?l={link}"
    try:
        etag = _req_raw(port, path)[2]["ETag"]
        timing_link.clear(link_path)
        assert _req_raw(port, path, {"If-None-Match": etag})[0] == 403, "event stop ends the link"
    finally:
        srv.shutdown()


def t_timing_page_is_gzipped_and_revalidated():
    srv, port, _lp, link, _hub = _timing_server()
    path = f"/console/timing?l={link}"
    try:
        st, body, h = _req_raw(port, path, {"Accept-Encoding": "gzip"})
        assert st == 200 and h.get("Content-Encoding") == "gzip", (st, h)
        assert "Live timing" in gzip.decompress(body).decode("utf-8")
        st, body, h = _req_raw(port, path, {"If-None-Match": h["ETag"]})
        assert st == 304 and body == b"", st
    finally:
        srv.shutdown()



def t_timing_revalidation_handles_lists_weak_tags_and_encodings():
    srv, port, _lp, link, _hub = _timing_server()
    path = f"/console/timing/data?l={link}"
    try:
        _s, _b, hg = _req_raw(port, path, {"Accept-Encoding": "gzip"})
        _s, _b, hp = _req_raw(port, path)
        assert hg["ETag"] != hp["ETag"], "gzip and plain bodies need their own strong ETags"
        for inm in (hg["ETag"], hp["ETag"], f'"other", {hg["ETag"]}', "W/" + hp["ETag"], "*"):
            st, _b, h = _req_raw(port, path, {"If-None-Match": inm})
            assert st == 304 and h.get("Vary") == "Accept-Encoding", (inm, st, h)
        st, _b, h = _req_raw(port, path, {"Accept-Encoding": "gzip;q=0"})
        assert st == 200 and "Content-Encoding" not in h, "q=0 refuses gzip"
        assert float(h["X-Data-Age"]) >= 0, h
    finally:
        srv.shutdown()


def t_a_failing_timing_build_answers_503_with_a_pace():
    srv, port, _lp, link, _hub = _timing_server()
    srv.race_svc.dashboard = lambda: 1 / 0
    try:
        st, _b, h = _req_raw(port, f"/console/timing/data?l={link}")
        assert st == 503 and float(h["X-Poll-After"]) >= 2.0, (st, h)
    finally:
        srv.shutdown()


def t_the_timing_page_is_built_once_under_concurrent_first_requests():
    import threading
    srv, port, _lp, link, _hub = _timing_server()
    real, calls = g.m.timing_feed.encode, []

    def slow(raw):
        calls.append(1)
        time.sleep(0.2)
        return real(raw)
    g.m.timing_feed.encode = slow
    try:
        threads = [threading.Thread(target=_req_raw, args=(port, f"/console/timing?l={link}")) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1, f"concurrent first viewers build the page once: {len(calls)}"
    finally:
        g.m.timing_feed.encode = real
        srv.shutdown()

class _SpyBudget:
    """Wraps the real Budget and keeps every served() call and the live instance."""
    made = []

    real_class = g.m.timing_feed.Budget

    def __init__(self, kbps):
        self.real, self.calls = _SpyBudget.real_class(kbps), []
        _SpyBudget.made.append(self)

    def served(self, nbytes, interval_s):
        self.calls.append(nbytes)
        self.real.served(nbytes, interval_s)

    def interval(self):
        return self.real.interval()


def _wait_for(cond, timeout=2.0):
    import time as _time
    end = _time.monotonic() + timeout
    while not cond() and _time.monotonic() < end:
        _time.sleep(0.01)


def _with_spy(**patch):
    saved = {k: getattr(g.m, k) for k in patch}
    budget, _SpyBudget.made[:] = g.m.timing_feed.Budget, []
    g.m.timing_feed.Budget = _SpyBudget
    for k, v in patch.items():
        setattr(g.m, k, v)
    try:
        srv, port, link_path, link, hub = _timing_server()
    finally:
        g.m.timing_feed.Budget = budget
        for k, v in saved.items():
            setattr(g.m, k, v)
    return srv, port, link, _SpyBudget.made[-1]


def t_the_budget_counts_headers_and_rejected_requests():
    srv, port, link, spy = _with_spy(TIMING_ABUSE_RPS=1)
    path = f"/console/timing/data?l={link}"
    try:
        st, body, h = _req_raw(port, path, {"Accept-Encoding": "gzip"})
        _wait_for(lambda: spy.calls)      # the server counts after the body is written
        assert spy.calls[0] == len(body) + g.m.timing_feed.HEADER_BYTES, (spy.calls, len(body))
        statuses = [_req_raw(port, path) for _ in range(12)]
        rejected = [x for x in statuses if x[0] == 429]
        assert rejected and all(float(x[2]["X-Poll-After"]) >= 5 for x in rejected), \
            "a rejected viewer is told when to come back"
        _wait_for(lambda: len(spy.calls) >= 13)
        assert len(spy.calls) == 13, f"rejected requests count as demand: {len(spy.calls)}"
    finally:
        srv.shutdown()



def t_retry_after_never_comes_before_the_pace():
    srv, port, link, spy = _with_spy(TIMING_ABUSE_RPS=1)
    spy.interval = lambda: 5.5
    try:
        rejected = [x for x in (_req_raw(port, f"/console/timing/data?l={link}") for _ in range(12)) if x[0] == 429]
        assert rejected and all(int(x[2]["Retry-After"]) >= 5.5 for x in rejected), \
            [x[2].get("Retry-After") for x in rejected]
    finally:
        srv.shutdown()

def t_internal_dashboard_routes_ignore_the_timing_budget():
    srv, port, link, spy = _with_spy(TIMING_BUDGET_KBPS=50)
    try:
        for _ in range(3000):
            spy.real.served(5000, 2.0)
        h = _req_raw(port, f"/console/timing/data?l={link}")[2]
        assert float(h["X-Poll-After"]) > 2.0, f"the budget must be exhausted for this test: {h}"
        for _ in range(2):
            srv.race_svc.tick()
            want = json.loads(json.dumps(srv.race_svc.dashboard() or {"v": 1, "entries": []}))
            st, body, h = _req_raw(port, "/drivers/dashboard")
            assert st == 200 and json.loads(body) == want, "the overlay gets the newest snapshot at once"
            for route in ("/drivers/dashboard", "/drivers/race", "/drivers/data"):
                st, _b, h = _req_raw(port, route)
                assert st == 200 and "X-Poll-After" not in h and "ETag" not in h, (route, st, h)
    finally:
        srv.shutdown()

class _FakePublisher:
    def __init__(self, connected=True):
        self.connected, self.reloads, self.fns = connected, 0, None

    def make(self, **fns):
        self.fns = fns
        return self

    def status(self):
        return {"configured": True, "connected": self.connected, "viewers": 4}

    def host_url(self):
        return "https://racecast-timing-demo.example.workers.dev"

    def reload(self):
        self.reloads += 1


FUNNEL_HOST = "relay.example.ts.net"


def t_timing_link_route_reports_host():
    pub = _FakePublisher()
    srv, port, _lp, link, _hub = _timing_server(timing_publisher=pub.make, funnel_host=FUNNEL_HOST)
    try:
        d = json.loads(_req(port, "/drivers/timing-link")[1])
        assert d["served"] == "hosted" and d["host"]["viewers"] == 4, d
        assert d["hosted_url"] == f"https://racecast-timing-demo.example.workers.dev/t/{link}", d
        body, age = pub.fns["body_fn"]()
        assert json.loads(body)["v"] == 1 and isinstance(age, float)
        assert pub.fns["link_fn"]() == link and pub.fns["served_fn"]() == "hosted"
        assert pub.fns["fallback_fn"]() == f"https://{FUNNEL_HOST}/console/timing?l={link}"
        assert d["funnel_url"] == pub.fns["fallback_fn"](), d
    finally:
        srv.shutdown()


def t_timing_link_route_without_host():
    srv, port, _lp, _link, _hub = _timing_server()
    try:
        d = json.loads(_req(port, "/drivers/timing-link")[1])
        assert d["served"] == "funnel" and d["hosted_url"] is None and d["host"] is None, d
    finally:
        srv.shutdown()


def t_funnel_data_moved_to_when_hosted():
    pub = _FakePublisher()
    srv, port, _lp, link, _hub = _timing_server(timing_publisher=pub.make, funnel_host=FUNNEL_HOST)
    try:
        data = json.loads(_req(port, f"/console/timing/data?l={link}")[1])
        assert data.get("moved_to") == f"https://racecast-timing-demo.example.workers.dev/t/{link}", data
    finally:
        srv.shutdown()


def t_funnel_data_stays_when_funnel_served_or_host_down():
    for served, connected in (("funnel", True), (None, False)):
        pub = _FakePublisher(connected=connected)
        srv, port, lp, link, _hub = _timing_server(timing_publisher=pub.make, funnel_host=FUNNEL_HOST)
        try:
            if served:
                timing_link.set_served(lp, served)
            data = json.loads(_req(port, f"/console/timing/data?l={link}")[1])
            assert "moved_to" not in data, (served, connected, data)
        finally:
            srv.shutdown()


def t_no_funnel_no_fallback_and_no_funnel_url():
    pub = _FakePublisher()
    srv, port, _lp, _link, _hub = _timing_server(timing_publisher=pub.make, funnel_host=lambda: None)
    try:
        d = json.loads(_req(port, "/drivers/timing-link")[1])
        assert d["funnel_url"] is None, d
        assert pub.fns["fallback_fn"]() is None, "hosted viewers are never sent to a Funnel that is off"
    finally:
        srv.shutdown()


def t_funnel_watch_reports_the_host_only_while_funnel_serves():
    import time as _time
    ts = g.m.tailscale
    saved = (ts.detect_magicdns_name, ts.funnel_on)
    calls = []
    try:
        ts.detect_magicdns_name = lambda *a, **k: calls.append(1) or FUNNEL_HOST
        for on, want in ((True, FUNNEL_HOST), (False, None)):
            ts.funnel_on = lambda *a, on=on, **k: on
            get = g.m._funnel_watch()
            first = get()
            _wait_for(lambda get=get, on=on: get() is not None or not on)
            _time.sleep(0.05)
            assert first is None and get() == want, (on, first, get())
        n = len(calls)
        for _ in range(20):
            get()
        assert len(calls) == n, "the cache answers; no tailscale call per request"
    finally:
        ts.detect_magicdns_name, ts.funnel_on = saved


def t_timing_host_reload_is_tailnet_only():
    pub = _FakePublisher()
    srv, port, _lp, _link, _hub = _timing_server(timing_publisher=pub.make, funnel_host=FUNNEL_HOST)
    try:
        assert _req(port, "/drivers/timing-host/reload", b"{}", {"Content-Type": "application/json"})[0] == 200
        assert pub.reloads == 1
        st = _req(port, f"/console/drivers/timing-host/reload?t={g._tok('dave')}", b"{}",
                  {"Content-Type": "application/json"})[0]
        assert st in (403, 404) and pub.reloads == 1, st
    finally:
        srv.shutdown()


def t_internal_dashboard_routes_ignore_a_dead_host():
    import threading
    import time as _time
    import timing_host
    import timing_publish

    def hang(*_a, **_k):
        _time.sleep(3)
        raise OSError("host unreachable")

    stop = threading.Event()

    def make(**fns):
        pub = timing_publish.Publisher(lambda: timing_host.Settings("https://h.example.workers.dev", "k"),
                                       connect=hang, **fns)
        threading.Thread(target=pub.run, args=(stop,), daemon=True).start()
        return pub
    srv, port, _lp, link, _hub = _timing_server(timing_publisher=make, funnel_host=FUNNEL_HOST)
    try:
        _time.sleep(0.2)
        for _ in range(3):
            srv.race_svc.tick()
            t0 = _time.monotonic()
            st, _b, _h = _req_raw(port, "/drivers/dashboard")
            assert st == 200 and _time.monotonic() - t0 < 1.0, "a hanging host never delays the overlay"
            assert _req_raw(port, f"/console/timing/data?l={link}")[0] == 200
    finally:
        stop.set()
        srv.shutdown()


def _audit_of(srv):
    import json as _json
    with open(driver_race.audit_path_for(srv.race_svc._path), encoding="utf-8") as fh:
        return [_json.loads(line) for line in fh]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

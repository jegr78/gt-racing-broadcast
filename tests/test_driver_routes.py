#!/usr/bin/env python3
"""The driver telemetry routes under /console/drive and on the tailnet (#1111).
Run: python3 tests/test_driver_routes.py"""
import gzip, json, os, struct, sys, tempfile, threading, time, urllib.error, urllib.request

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


def _serve(solo=True, segments=(), clock=None, recording_dir=None, enabled=lambda: True, **extra):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "driver-invite.json")
    from test_race_distance import TRACE
    race = driver_race.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "t"},
                               trace_for=lambda _i: TRACE, multi_lobby=lambda: not solo,
                               enabled=enabled, recording_dir=recording_dir)
    race.open_recording()
    hub = driver_ingest.Hub(multi_lobby=lambda: not solo, on_sample=race.update,
                            **({"clock": clock} if clock else {}))
    extra.setdefault("sender_script_path", os.path.join(ROOT, "src", "relay", "racecast-sender.py"))
    extra.setdefault("race_snapshot_page_path", os.path.join(ROOT, "src", "console", "race-snapshot.html"))
    srv = g._serve(solo=solo, hud_source=_Hud(), driver_hub=hub, driver_invite_path=path,
                   drive_page_path=os.path.join(ROOT, "src", "console", "drive.html"),
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


def t_tower_gap_column_reads_the_leader_gap():
    with open(os.path.join(ROOT, "src", "obs", "drivers.html"), encoding="utf-8") as fh:
        src = fh.read()
    col = src[src.index("function column("):src.index("function row(")]
    assert "e.gap_s" in col, "the tower column shows the gap to the leader"
    assert "e.interval_s" not in col, "the tower column no longer shows the interval"


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
        code, text, _h = _register(port, invite, entry="Team A #1", name="Ann")
        assert code == 409 and "already has its live senders" in text, \
            ("a full entry refuses the registration, so the invite page shows it", code, text)
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


def t_a_non_ascii_driver_code_is_refused_like_any_bad_code():
    srv, port, path, _hub = _serve()
    try:
        driver_invite.create(path)
        code, text, _h = _req(port, "/console/drive/ingest", tp.encode_batch([_wire(1)]),
                              {"X-Driver-Code": "D1:\u00e9.abc"})
        assert code == 403 and "register again" in text, (code, text)
    finally:
        srv.shutdown()


def t_a_missing_sender_script_never_names_a_server_path():
    srv, port, path, _hub = _serve(sender_script_path=os.path.join(tempfile.mkdtemp(), "gone.py"))
    try:
        invite = driver_invite.create(path)["id"]
        code, text, _h = _req(port, f"/console/drive/sender.py?i={invite}")
        assert code == 404 and "looked_for" not in text and "gone.py" not in text, \
            ("a Funnel-reachable 404 must not leak the producer's path", code, text)
    finally:
        srv.shutdown()


def t_a_reassigned_sender_keeps_uploading_after_its_typo_entry_leaves():
    srv, port, path, hub = _serve()
    try:
        invite = driver_invite.create(path)["id"]
        ENTRIES.append("Team Typo")
        try:
            issued = json.loads(_register(port, invite, entry="Team Typo", name="Max")[1])["code"]
            _req(port, "/console/drive/ingest", tp.encode_batch([_wire(1)]), {"X-Driver-Code": issued})
            assert hub.reassign(hub.connections()[0]["conn"], "Team B #2")
        finally:
            ENTRIES.remove("Team Typo")
        code, text, _h = _req(port, "/console/drive/ingest", tp.encode_batch([_wire(2)]),
                              {"X-Driver-Code": issued})
        assert code == 200, ("the roster check must use the reassigned entry", code, text)
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
        def stint_switch(self, final_laps=None, window=True, final_row=None):
            calls.append(final_laps if window else ("start", final_laps))
            rows_seen.append(final_row)
    rows_seen = []
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
    calls.clear()
    rows_seen.clear()
    Source.final_laps = lambda self: None
    stop = threading.Event()
    worker = threading.Thread(target=g.m.stint_switch_watch,
                              args=(relay, Svc(), stop, lambda: None, 0.01), daemon=True)
    worker.start()
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    worker.join(2)
    assert calls == [("start", None)] and rows_seen == [True], \
        f"the last row on air is passed without a count, so a later count reaches it: {calls} {rows_seen}"


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
        self.quota_low = False

    def make(self, **fns):
        self.fns = fns
        return self

    def status(self):
        return {"configured": True, "connected": self.connected, "viewers": 4,
                "quota_low": self.quota_low}

    def host_url(self):
        return "https://racecast-timing-demo.example.workers.dev"

    def reload(self):
        self.reloads += 1


FUNNEL_HOST = "relay.example.ts.net"


def t_the_invite_offers_the_funnel_base_when_funnel_serves_the_console():
    for host, base in ((FUNNEL_HOST, f"https://{FUNNEL_HOST}"), (None, None)):
        srv, port, path, _hub = _serve(funnel_host=lambda h=host: h)
        try:
            invite = driver_invite.create(path)["id"]
            d = json.loads(_req(port, "/drivers/invite")[1])
            assert d["id"] == invite and d["base"] == base, \
                f"drivers need the public Funnel address, not the desk's tailnet origin: {d}"
        finally:
            srv.shutdown()


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


def t_funnel_data_stays_when_the_host_quota_is_nearly_used():
    pub = _FakePublisher()
    pub.quota_low = True
    srv, port, _lp, link, _hub = _timing_server(timing_publisher=pub.make, funnel_host=FUNNEL_HOST)
    try:
        data = json.loads(_req(port, f"/console/timing/data?l={link}")[1])
        assert "moved_to" not in data, "Cloudflare stops serving soon: Funnel viewers must stay"
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


def t_funnel_watch_learns_the_magicdns_name_without_funnel():
    ts = g.m.tailscale
    saved = (ts.detect_magicdns_name, ts.funnel_on)
    try:
        ts.detect_magicdns_name = lambda *a, **k: FUNNEL_HOST
        ts.funnel_on = lambda *a, **k: False
        get = g.m._funnel_watch()
        assert get.magicdns() is None, "the first call answers from the empty cache"
        _wait_for(lambda: get.magicdns() is not None)
        assert get.magicdns() == FUNNEL_HOST and get() is None, (get.magicdns(), get())
    finally:
        ts.detect_magicdns_name, ts.funnel_on = saved


def t_a_polling_funnel_watch_learns_a_late_name_without_requests():
    ts = g.m.tailscale
    saved = (ts.detect_magicdns_name, ts.funnel_on)
    calls, stop = [], threading.Event()
    try:
        ts.detect_magicdns_name = lambda *a, **k: calls.append(1) or (FUNNEL_HOST if len(calls) > 2 else None)
        ts.funnel_on = lambda *a, **k: False
        get = g.m._funnel_watch(max_age_s=0.05, poll=stop)
        _wait_for(lambda: len(calls) > 3)
        assert get.magicdns() == FUNNEL_HOST, \
            ("the watch refreshes by itself, so the first request after Tailscale comes up finds the name", calls)
    finally:
        stop.set()
        time.sleep(0.2)                       # let the poll thread see the stop before the restore
        ts.detect_magicdns_name, ts.funnel_on = saved


def t_a_magicdns_name_learned_after_start_is_a_browser_host():
    known = {"name": None}

    def funnel_host():
        return None
    funnel_host.magicdns = lambda: known["name"]
    srv, port, path, _hub = _serve(funnel_host=funnel_host)
    try:
        issued = json.loads(_register(port, driver_invite.create(path)["id"],
                                      entry="Team A #1", name="Max")[1])["code"]
        code, text, _h = _req(port, "/console/drive/ingest", tp.encode_batch([_wire(1)]),
                              {"X-Driver-Code": issued, "Host": FUNNEL_HOST})
        assert code == 200, ("a sender authenticates by header, so the Host check never blocks it", code, text)
        assert _req(port, "/drivers/data", headers={"Host": FUNNEL_HOST})[0] == 403, \
            "an unknown name stays refused"
        known["name"] = FUNNEL_HOST           # Tailscale came up after the relay started
        code, text, _h = _req(port, "/drivers/data", headers={"Host": FUNNEL_HOST})
        assert code == 200, ("the late MagicDNS name must be accepted without a restart", code, text)
    finally:
        srv.shutdown()


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


def t_recording_routes_serve_both_desks_and_refuse_a_commentator():
    import csv as _csv, io as _io, json as _json
    hdr = {"Content-Type": "application/json"}
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp())
    try:
        for token, prefix in ((g._tok("bob"), "/console/drivers"),
                              (g._tok("dave"), "/console/race-control/drivers")):
            def url(tail, token=token, prefix=prefix):
                return f"{prefix}/race/recording{tail}" + ("&" if "?" in tail else "?") + f"t={token}"
            st, text, _h = _req(port, url(""))
            assert st == 200 and "running" in _json.loads(text), (st, text)
            st, text, _h = _req(port, url(""), _json.dumps({"action": "start"}).encode(), hdr)
            assert st == 200 and _json.loads(text)["recording"]["running"], text
            st, text, _h = _req(port, url(""), _json.dumps({"action": "stop"}).encode(), hdr)
            assert st == 200 and not _json.loads(text)["recording"]["running"], text
            assert _req(port, url(""), _json.dumps({"action": "bogus"}).encode(), hdr)[0] == 400
            at = f"{prefix}/race/recording/at?t={token}"
            assert _req(port, f"{at}&time=100")[0] == 404, "an empty recording has no sample"
            assert _req(port, f"{at}&time=nan")[0] == 400
            assert _req(port, f"{at}&time=inf")[0] == 400
            assert _req(port, at)[0] == 400
            st, text, h = _req(port, url("/csv"))
            assert st == 200 and h["Content-Type"].startswith("text/csv"), (st, h)
            assert "race-recording.csv" in h["Content-Disposition"], h
            assert next(_csv.reader(_io.StringIO(text)))[0] == "time_utc", text
            ev = _json.dumps({"id": 999, "decision": "confirm"}).encode()
            assert _req(port, url("/event"), ev, hdr)[0] == 404, "unknown event id"
            bad = _json.dumps({"id": "x", "decision": "confirm"}).encode()
            assert _req(port, url("/event"), bad, hdr)[0] == 400
        for prefix in ("/console/drivers", "/console/race-control/drivers"):
            alice = g._tok("alice")
            for tail, data in (("", None), ("/at?time=1", None), ("/csv", None),
                               ("", b'{"action":"start"}'), ("/event", b'{"id":1,"decision":"confirm"}')):
                head = {"Cookie": f"rc_console={alice}", **(hdr if data else {})}
                assert _req(port, f"{prefix}/race/recording{tail}", data, head)[0] == 403, (prefix, tail)
    finally:
        srv.shutdown()


def t_recording_routes_answer_503_without_a_recording_dir():
    srv, port, _p, _hub = _serve()
    try:
        director = g._tok("bob")
        assert _req(port, f"/console/drivers/race/recording?t={director}")[0] == 503
        assert _req(port, f"/console/drivers/race/recording/csv?t={director}")[0] == 503
        assert _req(port, f"/console/drivers/race/recording/at?t={director}&time=1")[0] == 503
        st, _t, _h = _req(port, f"/console/drivers/race/recording?t={director}", b'{"action":"start"}',
                          {"Content-Type": "application/json"})
        assert st == 503
    finally:
        srv.shutdown()


def t_recording_at_authenticates_with_t_and_reads_time():
    import json as _json
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp())
    try:
        director = g._tok("bob")
        _req(port, f"/console/drivers/race/recording?t={director}", b'{"action":"start"}',
             {"Content-Type": "application/json"})
        srv.race_svc.tick()
        assert srv.race_svc._recorder()._writer.flush(), "the sample reaches the store"
        now = time.time()
        st, text, _h = _req(port, f"/console/drivers/race/recording/at?t={director}&time={now}")
        assert st == 200 and "rows" in _json.loads(text), (st, text)
    finally:
        srv.shutdown()


def t_snapshot_page_is_served_to_both_desks():
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp())
    try:
        for token, prefix in ((g._tok("bob"), "/console/drivers"),
                              (g._tok("dave"), "/console/race-control/drivers")):
            st, text, h = _req(port, f"{prefix}/snapshot?t={token}&time=100")
            assert st == 200 and "<title>Race snapshot</title>" in text, (prefix, st)
            assert h["Cache-Control"] == "no-store", h
        st, text, _h = _req(port, "/drivers/snapshot?time=100")
        assert st == 200 and "<title>Race snapshot</title>" in text, "the tailnet panel opens it too"
        cross = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        st, _t, _h = _req(port, f"/console/race-control/drivers/snapshot?t={g._tok('dave')}", headers=cross)
        assert st == 200, "a snapshot link posted in the crew chat opens"
        alice = g._tok("alice")
        for prefix in ("/console/drivers", "/console/race-control/drivers"):
            assert _req(port, f"{prefix}/snapshot?t={alice}")[0] == 403, prefix
    finally:
        srv.shutdown()


def t_snapshot_links_use_time_never_the_token_parameter():
    import pathlib
    console = pathlib.Path(ROOT, "src", "console")
    page = (console / "race-snapshot.html").read_text(encoding="utf-8")
    desk = (console / "driver-desk.js").read_text(encoding="utf-8")
    for name, text in (("page", page), ("desk", desk)):
        assert "?time=" in text, f"the {name} names the sample time as time"
        assert "snapshot?t=" not in text and "at?t=" not in text, f"t is the console token ({name})"
    assert "innerHTML" not in page, "server strings go in through textContent"
    assert "./race/recording" in page, "relative fetches work under both desk prefixes"


def _raw_get(port, path):
    """One GET without following redirects: (status, headers)."""
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request("GET", path)
        r = c.getresponse()
        r.read()
        return r.status, dict(r.getheaders())
    finally:
        c.close()


def t_recording_routes_answer_503_when_the_store_cannot_open():
    d = tempfile.mkdtemp()
    blocker = os.path.join(d, "file")
    open(blocker, "w").close()
    srv, port, _p, _hub = _serve(recording_dir=os.path.join(blocker, "sub"))
    try:
        director = g._tok("bob")
        for tail in ("", "/csv", "/at?time=1"):
            sep = "&" if "?" in tail else "?"
            st, text, _h = _req(port, f"/console/drivers/race/recording{tail}{sep}t={director}")
            assert st == 503 and "unavailable" in text, (tail, st, text)
        st, _t, _h = _req(port, f"/console/drivers/race/recording?t={director}", b'{"action":"start"}',
                          {"Content-Type": "application/json"})
        assert st == 503, st
    finally:
        srv.shutdown()


def t_recording_event_rejects_bad_types_with_400():
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp())
    try:
        url = f"/console/drivers/race/recording/event?t={g._tok('bob')}"
        for body in (b'{"id":1,"decision":[]}', b'{"id":1.5,"decision":"confirm"}',
                     b'{"id":1e999,"decision":"confirm"}', b'{"id":[1],"decision":"confirm"}'):
            st, text, _h = _req(port, url, body, {"Content-Type": "application/json"})
            assert st == 400, (body, st, text)
    finally:
        srv.shutdown()


def t_a_manual_start_outside_race_mode_is_refused():
    import json as _json
    d = tempfile.mkdtemp()
    srv, port, _p, _hub = _serve(recording_dir=d, enabled=lambda: False)
    try:
        url = f"/console/race-control/drivers/race/recording?t={g._tok('dave')}"
        st, text, _h = _req(port, url)
        assert st == 200 and _json.loads(text)["running"] is False, (st, text)
        st, text, _h = _req(port, url, b'{"action":"start"}', {"Content-Type": "application/json"})
        assert st == 409 and _json.loads(text)["error"] == "recording runs in race mode only", (st, text)
        assert not [f for f in os.listdir(d) if f.startswith("race-recording")], os.listdir(d)
    finally:
        srv.shutdown()


def t_recording_routes_refuse_the_other_desks_role():
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp())
    try:
        hdr = {"Content-Type": "application/json"}
        for token, prefix in ((g._tok("bob"), "/console/race-control/drivers"),
                              (g._tok("dave"), "/console/drivers")):
            for tail, data in (("/race/recording", None), ("/race/recording/csv", None),
                               ("/race/recording/at?time=1", None), ("/snapshot", None),
                               ("/race/recording", b'{"action":"start"}'),
                               ("/race/recording/event", b'{"id":1,"decision":"confirm"}')):
                head = {"Cookie": f"rc_console={token}", **(hdr if data else {})}
                st = _req(port, f"{prefix}{tail}", data, head)[0]
                assert st == 403, f"a director reaches only /console/drivers, Race Control only " \
                                  f"/console/race-control/drivers: {prefix}{tail} gave {st}"
    finally:
        srv.shutdown()


def t_the_snapshot_page_sets_the_console_cookie_only_through_the_console():
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp())
    try:
        dave = g._tok("dave")
        st, _t, h = _req(port, f"/console/race-control/drivers/snapshot?t={dave}&time=5")
        cookie = h.get("Set-Cookie", "")
        assert st == 200 and "rc_console=" in cookie and "Path=/console" in cookie, h
        st, _t, h = _req(port, "/drivers/snapshot?t=junk&time=5")
        assert st == 200 and "Set-Cookie" not in h, "an unverified ?t= never becomes a cookie"
    finally:
        srv.shutdown()


def t_a_trailing_slash_on_the_snapshot_page_redirects():
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp())
    try:
        dave = g._tok("dave")
        st, h = _raw_get(port, f"/console/race-control/drivers/snapshot/?t={dave}&time=5")
        assert st == 302 and h["Location"] == f"/console/race-control/drivers/snapshot?t={dave}&time=5", (st, h)
        st, h = _raw_get(port, "//drivers/snapshot/?time=5")
        assert st == 302 and h["Location"] == "/drivers/snapshot?time=5", (st, h)
        st, h = _raw_get(port, "/drivers/snapshot?time=5")
        assert st == 200, st
    finally:
        srv.shutdown()


def t_the_snapshot_redirect_never_carries_a_line_break():
    loc = g.m._slashless_location("/drivers//snapshot/", "time=5&x=a\r\nSet-Cookie:%20y")
    assert "\r" not in loc and "\n" not in loc, loc
    assert loc.startswith("/drivers/snapshot?time=5&x=a"), loc
    assert g.m._slashless_location("//drivers/snapshot/", "") == "/drivers/snapshot"

def t_an_unauthenticated_snapshot_visit_with_oauth_gets_the_launcher():
    srv, port, _p, _hub = _serve(recording_dir=tempfile.mkdtemp(),
                                 discord_client_id="cid", discord_client_secret="secret")
    try:
        for path in ("/console/race-control/drivers/snapshot?time=5", "/console/drivers/snapshot"):
            st, text, h = _req(port, path)
            assert st == 200 and "<title>Race snapshot</title>" not in text, (path, st)
            assert "Set-Cookie" not in h or "rc_console=" not in h["Set-Cookie"], h
            with open(os.path.join(ROOT, "src", "console", "console.html"), encoding="utf-8") as fh:
                title = fh.read().split("<title>", 1)[1].split("</title>", 1)[0]
            assert f"<title>{title}</title>" in text, f"{path} serves the launcher"
        st, _t, _h = _req(port, "/console/race-control/drivers/race/recording/at?time=5")
        assert st == 401, "the data stays behind the login"
    finally:
        srv.shutdown()


def _console_src(name):
    with open(os.path.join(ROOT, "src", "console", name), encoding="utf-8") as fh:
        return fh.read()


def t_the_desk_never_waits_for_the_recording_before_the_race_table():
    desk = _console_src("driver-desk.js")
    body = desk[desk.index("async function refresh()"):desk.index("async function refreshInvite()")]
    assert "await call(\"/drivers/race/recording\"" not in body, "a slow recording call delays the race table"
    assert body.index("call(\"/drivers/race\"") < body.index("await"), \
        "the race fetch starts before the desk awaits anything"


def t_the_desk_links_the_whole_recording():
    desk = _console_src("driver-desk.js")
    assert "Browse recording" in desk and 'href("/drivers/snapshot")' in desk, \
        "the slider over the whole recording is reachable from the desk"


def t_the_desk_lapped_gap_breaks_after_the_dot():
    desk = _console_src("driver-desk.js")
    assert '"\\u00a0L" + (typeof e.gap_time_s === "number" ? "\\u00a0\\u00b7 " + secs(e.gap_time_s)' in desk, \
        "+1 L, NBSP, dot, plain space, time"


def t_the_desk_pills_do_not_wrap_inside():
    desk = _console_src("driver-desk.js")
    assert ".dd .race .pill{white-space:normal}" not in desk, "a pill keeps its words on one line"


def t_the_desk_forgets_suspicions_when_the_status_fails():
    desk = _console_src("driver-desk.js")
    fn = desk[desk.index("function renderRecording(rec)"):]
    assert fn.index("suspected = Object.create(null)") < fn.index("if (!rec) return"), \
        "a failed status poll leaves no stale Suspected DC marks"


def t_the_desk_swallows_a_refused_audio_resume():
    assert "audio.resume().catch(function () {})" in _console_src("driver-desk.js")


def t_the_snapshot_slider_follows_a_running_recording():
    page = _console_src("race-snapshot.html")
    assert "setInterval(loadSpan, 10000)" in page, "the span refreshes every 10 s"


def t_the_snapshot_page_shows_the_first_sample_a_later_span_brings():
    page = _console_src("race-snapshot.html")
    fn = page[page.index("async function loadSpan()"):page.index("(async function start()")]
    assert "if (!shown)" in fn and "show(" in fn, \
        "a page opened before any sample shows one once the span appears"
    show = page[page.index("async function show(t)"):page.index("var timer = null;")]
    assert "shown = true" in show, "only a rendered sample counts as shown"


def _audit_of(srv):
    import json as _json
    with open(driver_race.audit_path_for(srv.race_svc._path), encoding="utf-8") as fh:
        return [_json.loads(line) for line in fh]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

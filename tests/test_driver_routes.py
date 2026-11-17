#!/usr/bin/env python3
"""The driver telemetry routes under /console/drive and on the tailnet (#1111).
Run: python3 tests/test_driver_routes.py"""
import json, os, struct, sys, tempfile, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import driver_ingest  # noqa: E402
import driver_race  # noqa: E402
import driver_invite  # noqa: E402
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


def _serve(solo=True, segments=()):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "driver-invite.json")
    from test_race_distance import TRACE
    race = driver_race.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "t"},
                               trace_for=lambda _i: TRACE, multi_lobby=lambda: not solo)
    hub = driver_ingest.Hub(multi_lobby=lambda: not solo, on_sample=race.update)
    srv = g._serve(solo=solo, hud_source=_Hud(), driver_hub=hub, driver_invite_path=path,
                   drive_page_path=os.path.join(ROOT, "src", "console", "drive.html"),
                   sender_script_path=os.path.join(ROOT, "src", "relay", "racecast-sender.py"),
                   driver_segments=lambda: list(segments), driver_race_svc=race)
    return srv, srv.server_address[1], path, hub


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
    srv, port, path, _hub = _serve()
    try:
        invite = driver_invite.create(path)["id"]
        _req(port, "/drivers/race/settings", json.dumps({"formation": False}).encode(),
             {"Content-Type": "application/json"})
        codes = {e: json.loads(_register(port, invite, entry=e, name="N")[1])["code"] for e in ENTRIES}
        for i in range(0, 600, 6):                      # ten seconds at 60 Hz, sampled at 10 Hz
            for entry, behind in ((ENTRIES[0], 0.0), (ENTRIES[1], 100.0)):
                along = 50.0 * i / 60 - behind
                _req(port, "/console/drive/ingest", tp.encode_batch([_wire_at(i + 1, along, 1)]),
                     {"X-Driver-Code": codes[entry]})
        code, text, _h = _req(port, "/drivers/race")
        race = json.loads(text)
        assert code == 200 and race["state"] == "racing" and race["track"] == "t", race
        assert [r["entry"] for r in race["entries"]] == ENTRIES, race["entries"]
        assert race["settings"]["formation"] is False
        code, text, _h = _req(port, "/drivers/race/settings", json.dumps({"lap_count": 0}).encode(),
                              {"Content-Type": "application/json"})
        assert code == 400 and "lap_count" in text, (code, text)
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

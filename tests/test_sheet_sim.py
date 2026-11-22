#!/usr/bin/env python3
"""The simulated Sheet and its Apps Script webhook (#1230). Run: python3 tests/test_sheet_sim.py"""
import csv
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import sheet_sim  # noqa: E402
import simulation  # noqa: E402
import stream_target  # noqa: E402

for _key in [k for k in os.environ if k.startswith("RACECAST_")]:
    del os.environ[_key]
os.environ["RACECAST_SIMULATION"] = "1"
simulation.install_network_guard()

_spec = importlib.util.spec_from_file_location(
    "relay_feeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
_gg_spec = importlib.util.spec_from_file_location(
    "get_graphics", os.path.join(ROOT, "src", "relay", "get-graphics.py"))
gg = importlib.util.module_from_spec(_gg_spec)
_gg_spec.loader.exec_module(gg)

SCHEDULE = [["URL", "Streamer", "Stint"],
            ["sim://comms1", "Comms 1", "Stint 1"],
            ["", "Comms 2", "Stint 2"],
            ["", "Comms 3", "Stint 3"]]
SETUP = [["Stint", "Streamer", "Session", "Race Control", "Flag", "Team 1", "Team 2", "Team 3"],
         ["Stint 1", "Comms 1", "Race", "", "", "Team A #1", "Team B #2", ""]]
CONFIG = [["Teams", "Stints", "Streamers", "Session", "Race Control", "Flag"],
          ["Team A #1", "Stint 1", "Comms 1", "Race", "GREEN", "Green Flag"],
          ["Team B #2", "Stint 2", "Comms 2", "", "", ""]]
CREW = [["Name", "Commentator", "Director", "Producer", "Race Control", "Discord"],
        ["Sim Director", "FALSE", "TRUE", "FALSE", "FALSE", "sim_director"]]


class Clock:
    def __init__(self, now=1795000000.0):
        self.now = now

    def __call__(self):
        return self.now


def _sim(**kw):
    tabs = {"Schedule": SCHEDULE, "Qualifying": [["URL", "Streamer", "Stint"]],
            "Setup": SETUP, "Configuration": CONFIG, "Crew": CREW,
            "POV": [["URL", "Name"], ["", ""]]}
    tabs.update(kw.pop("tabs", {}))
    return sheet_sim.SheetSim(tabs=tabs, **kw).start()


def _csv_url(sim, tab, headers=False):
    url = simulation.sheet_csv_url("ignored-id", tab, sim.env(), headers=headers)
    assert url.startswith(sim.url), url
    return url


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")


def _rows(sim, tab):
    return list(csv.reader(io.StringIO(_get(_csv_url(sim, tab))[2])))


def _post(sim, payload, url=None):
    return json.loads(m.post_webhook(url or sim.push_url, payload, timeout=5))


def _status(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def t_env_is_a_valid_simulation_endpoint():
    with _sim() as sim:
        env = sim.env()
        assert env[simulation.FLAG] == "1", "a caller cannot forget simulation mode and reach Google"
        assert set(env) == {simulation.FLAG, simulation.SHEET_URL, simulation.SHEET_PUSH_URL}
        assert simulation.endpoint(simulation.SHEET_URL, env) == sim.url
        assert simulation.endpoint(simulation.SHEET_PUSH_URL, env) == sim.push_url
        assert not [p for p in simulation.problems(env) if "SHEET" in p], simulation.problems(env)
        assert "key=" in sim.push_url, "the push URL carries its key like the real one"


def t_gviz_serves_every_cell_quoted_and_rectangular():
    with _sim() as sim:
        status, ctype, body = _get(_csv_url(sim, "Schedule"))
        assert status == 200 and ctype.startswith("text/csv"), (status, ctype)
        assert body.splitlines()[0] == '"URL","Streamer","Stint"', body
        assert body.splitlines()[2] == '"","Comms 2","Stint 2"', body
        assert _get(_csv_url(sim, "Schedule", headers=True))[2] == body, "headers=1 reads the same rows"
        sim.set_tab("Ragged", [["a"], ["b", "c", ""], [], ["", ""]])
        assert _rows(sim, "Ragged") == [["a", ""], ["b", "c"]], "trailing blanks are trimmed, rows padded"


def t_gviz_reads_a_tab_with_a_space_and_an_unknown_tab_as_empty():
    with _sim(tabs={"Event Notes": [["Note"], ["Bring snacks"]]}) as sim:
        assert _rows(sim, "Event Notes") == [["Note"], ["Bring snacks"]]
        status, _, body = _get(_csv_url(sim, "Quali Times"))
        assert (status, body) == (200, ""), "an unseeded tab is empty, like the merged relay stand-in"


def t_gviz_needs_the_csv_export_query():
    with _sim() as sim:
        status, _ = _status(sim.url + "/gviz/tq?sheet=Schedule")
        assert status == 400, status
        status, _ = _status(sim.http_url + "/spreadsheets/d/other/gviz/tq?tqx=out:csv&sheet=Schedule")
        assert status == 404, "another sheet id is not this sheet"


def t_schedule_write_confirms_and_a_later_read_sees_it():
    with _sim() as sim:
        body = m.post_webhook(sim.push_url, {"action": "schedule", "row": 3, "url": "sim://comms2",
                                             "name": "Comms 2"}, timeout=5)
        assert json.loads(body) == {"ok": True, "action": "schedule", "v": 7}, body
        assert m.check_webhook_response(body, "schedule") == (True, None)
        parsed = m.ScheduleSource._parse_rows(_get(_csv_url(sim, "Schedule"))[2])
        assert ("sim://comms2", "Comms 2", "Stint 2", 3) in parsed, parsed
        assert sim.records("Schedule")[1]["URL"] == "sim://comms2", "records()[i] is physical row i+2"


def t_schedule_write_targets_qualifying_appends_and_rejects_out_of_range():
    with _sim() as sim:
        _post(sim, {"action": "schedule", "row": 2, "tab": "Qualifying", "url": "sim://quali",
                    "stint": "Qualifying"})
        assert sim.tab("Qualifying")[1] == ["sim://quali", "", "Qualifying"]
        assert sim.tab("Schedule") == SCHEDULE, "a qualifying write never touches the Schedule"
        _post(sim, {"action": "schedule", "row": 5, "url": "sim://comms4"})
        assert sim.tab("Schedule")[4][0] == "sim://comms4", "row last+1 appends"
        answer = _post(sim, {"action": "schedule", "row": 9, "url": "sim://x"})
        assert answer == {"error": "row out of range: 9"}, answer
        assert _post(sim, {"action": "schedule", "row": "0"}) == {"error": "row out of range: 0"}
        ok, err = m.check_webhook_response(json.dumps(answer).encode(), "schedule")
        assert not ok and "row out of range" in err, err


def t_schedule_without_a_stint_header_skips_the_stint_and_falls_back_to_a_b():
    with _sim(tabs={"Schedule": [["sim://comms1", "Comms 1"]]}) as sim:
        _post(sim, {"action": "schedule", "row": 1, "url": "sim://comms9", "name": "Comms 9",
                    "stint": "Stint 9"})
        assert sim.tab("Schedule") == [["sim://comms9", "Comms 9"]]


def t_bad_key_writes_nothing_and_is_logged():
    with _sim() as sim:
        wrong = sim.push_url.replace("key=", "key=wrong")
        assert _post(sim, {"action": "schedule", "row": 2, "url": "sim://x"}, url=wrong) == \
            {"error": "bad key"}
        assert sim.tab("Schedule") == SCHEDULE
        log = sim.writes()
        assert len(log) == 1 and log[0]["response"] == {"error": "bad key"}, log
        assert "key" not in json.dumps(log[0]["payload"]), "the key never reaches the log"


def t_setup_write_reaches_the_overlay_mirror_the_relay_reads():
    with _sim() as sim, tempfile.TemporaryDirectory() as tmp:
        hud = m.HudSource(_csv_url(sim, "Overlay"), _csv_url(sim, "Configuration"),
                          os.path.join(tmp, "hud.json"))
        ctl = m.SetupControl(sim.push_url, hud)
        assert hud.refresh() and hud.data()["streamer"] == "Comms 1", hud.data()
        ctl._push_setup("Streamer", "Comms 2")
        assert ctl.push_status == "ok", ctl.last_error
        assert hud.data()["streamer"] == "Comms 2", "the relay reads the write back from Overlay"
        assert sim.tab("Setup")[1][1] == "Comms 2", "the value cell sits below the header"
        ctl._push_team(2, "Team A")
        assert hud.data()["teams"][1]["name"] == "Team A", hud.data()["teams"]
        assert sim.tab("Setup")[1][6] == "Team A #1", "teams writes the verbatim label"


def t_overlay_mirror_keeps_a_seeded_overlay_row_layout():
    overlay = [["", "Stint", "old"], ["", "Round Top", "Round 1"], ["", "Teams P1", "", "stale"]]
    with _sim(tabs={"Overlay": overlay}) as sim:
        parsed = m.parse_overlay(_get(_csv_url(sim, "Overlay"))[2])
        assert parsed["stint"] == "Stint 1" and parsed["round_top"] == "Round 1", parsed
        assert parsed["teams"][0] == "Team A #1", parsed["teams"]


def t_setup_rejects_unknown_fields_and_missing_headers_all_or_nothing():
    with _sim() as sim:
        assert _post(sim, {"action": "setup", "fields": {"Weather": "Rain"}}) == \
            {"error": "unknown setup field: Weather"}
        sim.set_tab("Setup", [["Stint", "Streamer"], ["Stint 1", "Comms 1"]])
        answer = _post(sim, {"action": "setup", "fields": {"Stint": "Stint 2", "Flag": "Green Flag"}})
        assert answer == {"error": "header not found in Setup tab: Flag"}, answer
        assert sim.tab("Setup")[1][0] == "Stint 1", "nothing is written when one header is missing"


def t_teams_checks_the_slot():
    with _sim() as sim:
        assert _post(sim, {"action": "teams", "slot": 4, "name": "x"}) == {"error": "slot out of range: 4"}
        sim.set_tab("Setup", [["Stint"], [""]])
        assert _post(sim, {"action": "teams", "slot": 1, "name": "x"}) == \
            {"error": "header not found in Setup tab: Team 1"}


def t_pov_write_lands_in_row_two_by_header():
    with _sim(tabs={"POV": [["Name", "URL"], ["", ""]]}) as sim:
        _post(sim, {"action": "pov", "url": "sim://pov", "name": "Driver Cam"})
        assert sim.tab("POV") == [["Name", "URL"], ["Driver Cam", "sim://pov"]]
        sim.set_tab("POV", None)
        assert _post(sim, {"action": "pov", "url": ""}) == {"error": "tab not found: POV"}


def t_relay_schedule_and_pov_saves_reach_the_sheet():
    with _sim() as sim, tempfile.TemporaryDirectory() as tmp:
        hud = m.HudSource(_csv_url(sim, "Overlay"), _csv_url(sim, "Configuration"),
                          os.path.join(tmp, "hud.json"))
        hud.refresh()
        ctl = m.SetupControl(sim.push_url, hud)
        ctl.autostart_worker = False
        assert ctl.schedule_set(2, url="sim://comms1b", name="Comms 2").get("ok")
        assert ctl.pov_set("sim://pov", name="Cam").get("ok")
        assert ctl.drain_saves() == 2 and ctl.push_status == "ok", ctl.last_error
        assert sim.tab("Schedule")[1][:2] == ["sim://comms1b", "Comms 2"]
        assert sim.tab("POV")[1] == ["sim://pov", "Cam"]
        assert [w["action"] for w in sim.writes()] == ["schedule", "pov"]


def t_crew_writes_booleans_extends_headers_and_deletes():
    with _sim() as sim, tempfile.TemporaryDirectory() as tmp:
        crew = m.CrewSource(_csv_url(sim, "Crew"), os.path.join(tmp, "crew.json"))
        ctl = m.SetupControl(sim.push_url, None, crew_source=crew)
        assert ctl.crew_set(2, name="Sim Marshal", race_control=True, discord="sim_marshal",
                            race_engineer=False) == {"ok": True, "row": 2}
        header = sim.tab("Crew")[0]
        assert header[-1] == "Race Engineer", "a missing header column is appended"
        row = dict(zip(header, sim.tab("Crew")[2], strict=True))
        assert row["Race Control"] == "TRUE" and row["Director"] == "FALSE", row
        assert row["Discord"] == "sim_marshal" and row["Race Engineer"] == "FALSE", row
        names = [r[0] for r in crew._parse_full(_get(_csv_url(sim, "Crew"))[2])]
        assert names == ["Sim Director", "Sim Marshal"], names
        assert _post(sim, {"action": "crew", "row": 1, "name": " "}) == {"error": "crew: name is required"}
        assert _post(sim, {"action": "crew", "row": "x"}) == {"error": "crew: row must be >= 1"}
        assert _post(sim, {"action": "crew", "row": 0, "name": "A"}) == {"error": "crew: row must be >= 1"}
        assert ctl.crew_delete(1) == {"ok": True, "row": 1}
        assert [r[0] for r in sim.tab("Crew")[1:]] == ["Sim Marshal"], "rows below shift up"


def t_crew_creates_its_tab_and_reads_js_truthiness():
    with _sim(tabs={"Crew": None}) as sim:
        _post(sim, {"action": "crew", "row": 1, "name": "A", "director": "false", "producer": 0})
        header, row = sim.tab("Crew")
        assert header == ["Name", "Commentator", "Director", "Producer", "Race Control", "Discord"]
        assert row == ["A", "FALSE", "TRUE", "FALSE", "FALSE", ""], "a non-empty string is truthy in JS"


def t_timer_round_trips_through_the_relay_parser():
    clock = Clock()
    with _sim(clock=clock) as sim:
        state = {"end": 1795003600.0, "duration": 3600, "visible": True, "remaining": None,
                 "updated": 0.0}
        body = m.post_webhook(sim.push_url, m.TimerStore._payload(state), timeout=5)
        assert json.loads(body) == {"ok": True, "action": "timer", "v": 7}
        read = m.parse_timer_tab(_get(_csv_url(sim, "Timer"))[2])
        assert read["end"] == state["end"] and read["duration"] == 3600 and read["visible"], read
        assert read["updated"] == clock.now, "Updated (UTC) is the script's own clock"
        assert sim.tab("Timer")[3] == ["Updated (UTC)", "2026-11-18T11:06:40.000Z"], sim.tab("Timer")


def t_stream_key_is_read_not_logged_and_answers_the_probe():
    with _sim(stream_keys={"key1": "sim-stream-1"}) as sim:
        body = m.post_webhook(sim.push_url, {"action": "get_stream_key", "ref": "key1"}, timeout=5)
        assert stream_target.parse_stream_key_response(body) == ("sim-stream-1", "")
        missing = _post(sim, {"action": "get_stream_key", "ref": "key9"})
        assert missing == {"ok": False, "action": "get_stream_key", "error": "no key for ref 'key9'"}
        assert m.probe_webhook(sim.push_url), "the recovery probe sees a running script"
        assert sim.writes() == [], "key reads stay out of the write log"
        state = json.dumps(sheet_sim.fetch(sim.http_url, "/_sim/state"))
        assert "sim-stream-1" not in state and json.loads(state)["key_reads"] == 3, state


def t_unknown_and_missing_actions_and_bad_json():
    with _sim() as sim:
        assert _post(sim, {"action": "lights"}) == {"error": "unknown action: lights"}
        assert _post(sim, {"visible": "FALSE"}) == {"ok": True, "action": "timer", "v": 7}
        status, body = _status(sim.push_url, data=b"{nope", headers={"Content-Type": "application/json"})
        assert status == 200 and json.loads(body)["error"].startswith("SyntaxError"), body
        ok, err, _ = m.push_webhook_retrying(sim.push_url, {"action": "lights"}, "lights",
                                             attempts=1, timeout=5)
        assert not ok and "unknown action" in err, err


def t_refuses_requests_a_web_page_could_send():
    with _sim() as sim:
        url = _csv_url(sim, "Schedule")
        assert _status(url, headers={"Origin": "http://example.test"})[0] == 403
        assert _status(url, headers={"Host": "sheet.example.test"})[0] == 403
        assert _status(sim.push_url, data=b"{}", headers={"Content-Type": "text/plain"})[0] == 415
        assert sim.writes() == [], "a refused request never reaches the script"


def t_refuses_an_oversized_body_before_reading_it():
    with _sim() as sim, socket.create_connection(("127.0.0.1", sim.port), timeout=5) as conn:
        conn.sendall((f"POST {sheet_sim.EXEC_PATH}?key=sim HTTP/1.1\r\nHost: 127.0.0.1:{sim.port}\r\n"
                      "Content-Type: application/json\r\nContent-Length: 999999999\r\n\r\n").encode())
        assert conn.recv(64).startswith(b"HTTP/1.1 413"), "a body over the limit is refused"
        assert sim.writes() == []


def t_refuses_a_content_length_that_is_no_number():
    with _sim() as sim, socket.create_connection(("127.0.0.1", sim.port), timeout=5) as conn:
        conn.sendall((f"POST {sheet_sim.EXEC_PATH}?key=sim HTTP/1.1\r\nHost: 127.0.0.1:{sim.port}\r\n"
                      "Content-Type: application/json\r\nContent-Length: ten\r\n\r\n").encode())
        assert conn.recv(64).startswith(b"HTTP/1.1 400"), "a non-numeric Content-Length is refused"


def t_fetch_stays_on_loopback():
    try:
        sheet_sim.fetch("http://sheet.example.test", "/_sim/state")
    except ValueError:
        return
    raise AssertionError("fetch must refuse a non-loopback URL")


def t_a_payload_that_is_no_object_is_an_error():
    with _sim() as sim:
        answer = _post(sim, ["schedule"])
        assert set(answer) == {"error"} and answer["error"].startswith("TypeError"), answer


def t_missing_tabs_and_headers_answer_the_script_errors():
    with _sim(tabs={"Setup": None, "Schedule": None}) as sim:
        assert _post(sim, {"action": "setup", "fields": {"Stint": "Stint 1"}}) == {"error": "tab not found: Setup"}
        assert _post(sim, {"action": "schedule", "row": 1}) == {"error": "tab not found: Schedule"}


def t_crew_delete_past_the_last_row_changes_nothing():
    with _sim() as sim:
        assert _post(sim, {"action": "crew", "row": 5, "delete": True})["ok"] is True
        assert sim.tab("Crew") == CREW


def t_channel_tab_and_stream_key_set_the_obs_target():
    with _sim(tabs={"Channel": [["Platform", "Channel"], ["YouTube", "sim://league"]]},
              stream_keys={"key1": "sim-stream-1"}) as sim:
        applied = []

        def set_service(platform, key):
            applied.append((platform, key))
            return True, "set"
        ok, note = m.apply_stream_service_for_ref("key1", _csv_url(sim, "Channel"), sim.push_url,
                                                  set_service)
        assert ok and applied == [("youtube", "sim-stream-1")], (ok, note, applied)
        assert "sim-stream-1" not in note, note


def t_assets_tab_reaches_the_graphics_reader():
    link = "https://drive.google.com/file/d/simfile1/view"
    with _sim(tabs={"Assets": [["Name", "Link"], ["Standby", link]]}) as sim:
        saved = dict(os.environ)
        os.environ.update(sim.env())
        try:
            rows = list(csv.reader(io.StringIO(gg.fetch_assets_csv("x", "Assets", timeout=5))))
        finally:
            os.environ.clear()
            os.environ.update(saved)
        assert gg.graphics_from_csv(rows) == {"Standby": link}, rows


def t_check_endpoints_refuse_a_bad_tab():
    with _sim() as sim:
        url = sim.http_url + "/_sim/tab"
        json_ct = {"Content-Type": "application/json"}
        assert _status(url, json.dumps({"name": 5}).encode(), json_ct)[0] == 400
        assert _status(url, json.dumps({"name": "X", "rows": ["a"]}).encode(), json_ct)[0] == 400
        assert sim.tab("X") is None


def t_check_endpoints_seed_tabs_and_list_writes():
    with _sim() as sim:
        sheet_sim.fetch(sim.http_url, "/_sim/tab", {"name": "Channel", "csv": "Platform,URL\nYouTube,sim://yt\n"})
        assert _rows(sim, "Channel") == [["Platform", "URL"], ["YouTube", "sim://yt"]]
        sheet_sim.fetch(sim.http_url, "/_sim/tab", {"name": "Graphics", "rows": [["Name", "Link"]]})
        _post(sim, {"action": "pov", "url": "sim://a"})
        _post(sim, {"action": "pov", "url": "sim://b"})
        got = sheet_sim.fetch(sim.http_url, "/_sim/writes?since=1")
        assert [w["payload"]["url"] for w in got["writes"]] == ["sim://b"] and got["seq"] == 2, got
        tab = sheet_sim.fetch(sim.http_url, "/_sim/tab?name=Crew")
        assert tab["records"][0]["Name"] == "Sim Director", tab
        state = sheet_sim.fetch(sim.http_url, "/_sim/state")
        assert {"Channel", "Graphics", "Overlay"} <= set(state["tabs"]), state


def t_cli_seeds_tabs_from_a_directory_and_writes_a_ready_file():
    with tempfile.TemporaryDirectory() as tmp:
        tabs = os.path.join(tmp, "tabs")
        os.mkdir(tabs)
        with open(os.path.join(tabs, "Schedule.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write("URL,Streamer\nsim://comms1,Comms 1\n")
        ready = os.path.join(tmp, "sheet.json")
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "event-sim", "fakes",
                                                              "sheet_sim.py"),
                                 "--port", "0", "--tabs", tabs, "--ready-file", ready],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            assert "sheet-sim" in proc.stdout.readline().decode("utf-8", "replace")
            with open(ready, encoding="utf-8") as fh:
                info = json.load(fh)
            env = info["env"]
            body = _get(simulation.sheet_csv_url("x", "Schedule", env))[2]
            assert body == '"URL","Streamer"\n"sim://comms1","Comms 1"\n', body
            assert info["push_url"] == env[simulation.SHEET_PUSH_URL]
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()


class Sleeps(list):
    def __call__(self, seconds):
        self.append(seconds)


def t_an_injected_script_error_writes_nothing_and_counts_down():
    with _sim() as sim:
        sim.fail("action:schedule", error="Service invoked too many times for one day", times=1)
        ok, err, _ = m.push_webhook_retrying(sim.push_url, {"action": "schedule", "row": 2, "url": "sim://x"},
                                             "schedule", attempts=1, timeout=5)
        assert not ok and "too many times" in err, err
        assert sim.tab("Schedule") == SCHEDULE and sim.writes() == [], "the script never ran"
        assert _post(sim, {"action": "schedule", "row": 2, "url": "sim://x"})["ok"] is True, "times=1 is spent"
        assert sim.snapshot()["faults_hit"] == 1


def t_an_injected_status_hits_only_its_tab():
    with _sim() as sim:
        sim.fail("tab:Schedule", status=500)
        assert _status(_csv_url(sim, "Schedule"))[0] == 500
        assert _rows(sim, "Crew") == CREW, "other tabs still read"
        sim.clear_faults()
        assert _rows(sim, "Schedule")[0] == SCHEDULE[0]


def t_an_injected_delay_uses_the_sleep_seam():
    sleeps = Sleeps()
    with _sim(sleep=sleeps, faults=[{"target": "push", "delay": 12}]) as sim:
        assert _post(sim, {"action": "pov", "url": "sim://a"})["ok"] is True, "a delay alone still answers"
        _rows(sim, "Schedule")
        assert sleeps == [12], sleeps


def t_faults_over_http_set_list_and_clear():
    with _sim() as sim:
        got = sheet_sim.fetch(sim.http_url, "/_sim/fail", {"target": "read", "status": 503})
        assert got["faults"] == [{"target": "read", "delay": 0, "status": 503, "error": None,
                                  "times": None}], got
        assert _status(_csv_url(sim, "Crew"))[0] == 503
        req = urllib.request.Request(sim.http_url + "/_sim/fail", data=b"", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read())["faults"] == [], "an empty body clears every fault"
        assert _rows(sim, "Crew") == CREW
        json_ct = {"Content-Type": "application/json"}
        for bad in ({"target": 5}, {"target": "push", "status": 99}, {"target": "push", "delay": -1},
                    {"target": "push", "times": 0}, {"target": "push", "error": 5}):
            assert _status(sim.http_url + "/_sim/fail", json.dumps(bad).encode(), json_ct)[0] == 400, bad
        assert sim.snapshot()["faults"] == []


def t_an_unknown_tab_can_answer_with_the_first_tab():
    with _sim(unknown_tab="first") as sim:
        assert _rows(sim, "Nope") == SCHEDULE, "gviz may answer an unknown name with the first sheet"
        assert _rows(sim, "Crew") == CREW


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

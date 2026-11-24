#!/usr/bin/env python3
"""The probe stage 2 kit, monitor and report (#1116). Run: python3 tests/test_probe_stage2.py"""
import io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import probe_stage2 as ps  # noqa: E402

INVITE = "https://host.example/console/drive?i=INVITEID"
SCRIPT = "https://host.example/console/drive/sender.py?i=INVITEID"


def t_every_platform_sheet_has_the_download_the_check_and_the_stop():
    for platform in ps.PLATFORMS:
        text = ps.kit(INVITE, SCRIPT, platform)
        assert SCRIPT in text and INVITE in text, f"{platform}: the sheet links the script and the invite"
        assert "--check" in text and "RESULT: OK" in text, f"{platform}: the sheet has the self-check"
        assert "## Stop it" in text, f"{platform}: the sheet says how to stop the sender"
        assert "leave out the command line" in text, f"{platform}: the code stays out of what a driver posts"
        assert text.isascii(), f"{platform}: plain ASCII for every console and chat"
    assert "py racecast-sender.py" in ps.kit(INVITE, SCRIPT, "windows")
    assert "curl.exe -o" in ps.kit(INVITE, SCRIPT, "windows"), "PowerShell's curl is an alias"
    iphone = ps.kit(INVITE, SCRIPT, "iphone")
    assert "`python3 racecast-sender.py --url ... --code ... --console <PlayStation IP>`" in iphone, \
        "a-Shell cannot discover the console: its command needs --console"
    assert "--code ... --console" not in ps.kit(INVITE, SCRIPT, "macos"), "discovery works on a Mac"
    android = ps.kit(INVITE, SCRIPT, "android")
    assert "termux-wake-lock" in android and "termux-wake-unlock" in android and "battery" in android
    assert "python racecast-sender.py" in android, "Termux names it python"
    assert "ufw allow 33740/udp" in ps.kit(INVITE, SCRIPT, "linux")


def _conn(conn, name, entry, received, sender_rx, t_lost=0, latest=None, live=True):
    return {"conn": conn, "entry": entry, "segment": "Stint 1", "name": name, "live": live,
            "idle_s": 0.1, "packets": {"received": received, "lost": t_lost, "reordered": 0,
                                       "decrypt_failed": 0, "parse_failed": 0},
            "duplicates": received // 100,
            "packets_per_s": 59.0,
            "sender_stats": {"received": sender_rx, "posted": 10, "post_ms_p50": 80, "post_ms_p95": 210,
                             "code": "D1:secret.sig"},
            "latest": latest}


def _race(entries, window=False, state="racing"):
    return {"state": state, "change_window": window, "red_flag": None, "lap": {"leader": 2},
            "fastest": None, "entries": entries}


def _row(entry, conn, driver, dist, laps, status="racing", pos=None, box=False):
    return {"entry": entry, "conn": conn, "driver": driver, "distance_m": dist, "laps": laps,
            "status": status, "in_box": box, "pit_stops": 0, "gt7_position": pos,
            "position_mismatch": False, "last_lap_ms": None, "finished": False}


def t_a_record_carries_no_code_key_or_connection_id():
    probe = {"key": "PROBEKEY", "senders": {"Spectator": {"packets": {"received": 5}, "kinds": {"A": 5},
                                                           "samples": [{"lap": 1, "race_position": 3}]}}}
    rec = ps.compact(100.0, {"connections": [_conn("conn-abcdef", "Ann", "Team A", 600, 600)]},
                     _race([_row("Team A", "conn-abcdef", "Ann", 100.0, 1)]), probe)
    text = json.dumps(rec)
    for secret in ("PROBEKEY", "D1:secret", "conn-abcdef"):
        assert secret not in text, f"{secret} must not reach the results folder: {text}"
    assert rec["conns"][0]["tag"] == rec["race"]["entries"][0]["tag"] == ps.tag("conn-abcdef"), \
        "the hash still joins a sender to its race row"
    assert rec["probe"]["Spectator"]["latest"]["race_position"] == 3
    assert "PROBEKEY" not in json.dumps(ps.redact_probe(probe))


def _polls():
    """Two drivers: Ann (Team A) drives stint 1, Ben takes over in a change window; Cid
    (Team B) drives through. 0x84 is set for Team A only. Polls every 10 s."""
    polls, t = [], 1000.0
    for i in range(13):
        a_conn, a_name = ("ca", "Ann") if i < 8 else ("cb", "Ben")
        dist_a = 500.0 + 300.0 * i if i < 6 else 2000.0          # stands in the box from poll 6
        if i >= 8:
            dist_a = 2000.0 + 300.0 * (i - 8)
        conns = [_conn("ca", "Ann", "Team A", 600 * min(i, 7), 600 * min(i, 7),
                       latest={"lap": 1 + i // 4, "race_position": 1, "car_count": 2, "best_ms": None,
                               "last_ms": None}, live=i < 8)]
        if i >= 7:
            conns.append(_conn("cb", "Ben", "Team A", 600 * (i - 7), 600 * (i - 7),
                               latest={"lap": 0 if i < 9 else 1, "race_position": 1, "car_count": 2,
                                       "best_ms": 95000, "last_ms": None}))
        # Cid's device loses a third of its packets in minute 1
        rx_b = 600 * i if i < 6 else 3600 + 400 * (i - 6)
        conns.append(_conn("cc", "Cid", "Team B", rx_b, rx_b,
                           latest={"lap": 1 + i // 4, "race_position": None, "car_count": None,
                                   "best_ms": None, "last_ms": None}))
        rows = [_row("Team A", a_conn, a_name, dist_a, 1 + i // 5, "box" if 6 <= i < 8 else "racing",
                     pos=1, box=6 <= i < 8),
                _row("Team B", "cc", "Cid", 400.0 + 280.0 * i - (3000.0 if i == 11 else 0.0), 1 + i // 5,
                     pos=None)]
        if i == 12:
            rows[1]["laps"] = 0                                      # a lap count stepping back
        polls.append(ps.compact(t, {"connections": conns}, _race(rows, window=6 <= i <= 9), None))
        t += 10.0
    return polls


def t_the_change_timeline_finds_the_change_and_the_carried_anchor():
    marks = [{"t": 1000.0, "text": "block 3 stint 1"}, {"t": 1055.0, "text": "block 3 change"}]
    events = ps.changes(_polls(), marks)
    kinds = [e["kind"] for e in events]
    assert kinds == ["window_open", "change", "window_closed"], kinds
    change = events[1]
    assert (change["entry"], change["from"], change["to"]) == ("Team A", "Ann", "Ben"), change
    assert change["old_status"] == "box" and change["old_in_box"] and change["in_window"], change
    assert change["distance_before"] == 2000.0 and change["distance_after"] == 2000.0, change
    assert change["anchor_carried"] is True and change["block"] == "block 3 change", change


def t_a_race_reset_starts_the_change_timeline_over():
    polls = _polls()
    reset = [{"t": polls[8]["t"] - 1.0, "text": "race reset"}]
    assert [e["kind"] for e in ps.changes(polls, reset) if e["kind"] == "change"] == [], \
        "after a reset the first connection of an entry is no change"
    marks = [{"t": 990.0, "text": "block B"}] + reset
    events = ps.changes(polls, marks)
    assert all(e["block"] == "block B" for e in events), f"an observation names no block: {events}"


def t_a_change_that_closes_the_window_counts_inside_it():
    polls = _polls()
    for i, rec in enumerate(polls):
        rec["race"]["change_window"] = 6 <= i <= 7       # the relay closes it with the change
    events = ps.changes(polls)
    assert [e["kind"] for e in events] == ["window_open", "change", "window_closed"], events
    assert events[1]["in_window"] is True, f"the change ended the window, it did not miss it: {events[1]}"


def t_a_distance_that_moves_away_at_the_change_is_no_carried_anchor():
    polls = _polls()
    for rec in polls[8:]:
        rec["race"]["entries"][0]["distance_m"] -= 1500.0
    change = [e for e in ps.changes(polls) if e["kind"] == "change"][0]
    assert change["anchor_carried"] is False, change
    assert not [j for j in ps.jumps(polls) if j["entry"] == "Team A"], \
        "the step at a change is the change's finding, never also a jump"


def t_hz_per_minute_shows_the_device_rate_and_the_low_minute():
    hz = ps.hz_by_minute(_polls())
    cid = hz[ps.tag("cc")]["rows"]
    assert [r["minute"] for r in cid] == [0, 1, 2], cid
    assert cid[0]["device_hz"] == 60.0, cid[0]
    assert cid[1]["device_hz"] < ps.PASS_HZ, f"the minute in which Cid's rate fell: {cid[1]}"
    text = ps.report_md(ps.analyse(_polls()))
    assert "Cid (Team B), 2 low minutes" in text, text


def t_positions_compare_the_order_of_0x84_with_the_relay_order():
    polls = _polls()
    for rec in polls:
        rec["race"]["entries"][0]["gt7_position"] = 3         # cars without a sender sit between
        rec["race"]["entries"][1]["gt7_position"] = 7
    rows = {r["entry"]: r for r in ps.positions(polls)}
    assert rows["Team A"]["agree"] == rows["Team A"]["with_0x84"], \
        f"GT7's 3 and 7 are the relay's first and second among the senders: {rows['Team A']}"
    polls[3]["race"]["entries"][0]["gt7_position"] = 9         # GT7 puts A behind B once
    rows = {r["entry"]: r for r in ps.positions(polls)}
    a, b = rows["Team A"], rows["Team B"]
    assert a["agree"] == a["with_0x84"] - 1 and b["agree"] == b["with_0x84"] - 1, (a, b)
    assert a["values"] == [3, 9], a
    assert "flagged" not in a and "relay_mismatch" in a, a


def t_an_entry_without_0x84_has_no_agreement():
    b = {r["entry"]: r for r in ps.positions(_polls())}["Team B"]
    assert b["with_0x84"] == 0 and b["agree_pct"] is None, f"no 0x84 is no agreement: {b}"
    counts = ps.car_counts(_polls())
    assert counts[ps.tag("ca")]["car_counts"] == [2] and counts[ps.tag("cc")]["with_position"] == 0


def t_lap_steps_back_and_distance_jumps_are_listed_per_connection():
    found = ps.jumps(_polls())
    kinds = sorted((j["entry"], j["kind"]) for j in found)
    assert ("Team B", "laps_back") in kinds and ("Team B", "distance_jump") in kinds, kinds
    assert not [j for j in found if j["entry"] == "Team A"], \
        f"a driver change is no jump: the new connection starts its own series: {found}"


def t_a_race_reset_is_no_lap_or_distance_step():
    polls = _polls()
    for rec in polls[11:]:
        rec["race"]["entries"][0]["laps"] = 0
        rec["race"]["entries"][0]["distance_m"] = 10.0
    reset = [{"t": polls[10]["t"] + 1.0, "text": "race reset"}]
    found = [j for j in ps.jumps(polls, reset) if j["entry"] == "Team A"]
    assert found == [], f"the reset starts the race over: {found}"
    assert [j for j in ps.jumps(polls) if j["entry"] == "Team A"], "without the reset mark it is a step back"


def t_a_best_before_the_first_lap_is_marked_carried():
    rows = ps.best_laps(_polls())[ps.tag("cb")]["rows"]
    assert rows and rows[0]["best_ms"] == 95000 and rows[0]["before_a_lap"] is True, rows


def t_a_sender_that_stopped_is_measured_over_the_time_it_sent():
    ann = {r["name"]: r for r in ps.sender_summary(_polls())}["Ann"]
    assert ann["device_hz"] == 60.0 and ann["relay_hz"] == 60.0, \
        f"the relay lists a stopped connection for minutes; its rate covers the sending time: {ann}"
    rows = ps.hz_by_minute(_polls())[ps.tag("ca")]["rows"]
    assert all(r["device_hz"] == 60.0 for r in rows), f"no low minute after the sender stopped: {rows}"


def _idle_polls():
    """Ann's device: 50 s idle before it sends, then 60 Hz with a 30 s gap in the middle."""
    polls, rx = [], 0
    for i in range(141):
        if 50 <= i < 80 or i >= 110:
            rx += 60
        polls.append(ps.compact(1000.0 + i, {"connections": [_conn("ca", "Ann", "Team A", rx, rx)]}, None, None))
    return polls


def t_idle_time_never_dilutes_the_rate():
    ann = ps.sender_summary(_idle_polls())[0]
    assert ann["device_hz"] == 60.0 and ann["relay_hz"] == 60.0, f"only the seconds it sent count: {ann}"
    assert ann["idle_s"] == 79.0, f"140 s listed, 61 s of them sending: {ann}"
    rows = ps.hz_by_minute(_idle_polls())[ps.tag("ca")]["rows"]
    assert all(r["device_hz"] == 60.0 for r in rows if r["device_hz"] is not None), rows
    assert [r["idle_s"] > 0 for r in rows] == [True, True, False], f"idle minutes are marked: {rows}"
    text = ps.report_md(ps.analyse(_idle_polls()))
    assert "idle" in text and "0 low minutes, minutes without a gap: 1" in text, text


def t_a_restarted_sender_counts_from_zero_again():
    polls = []
    for i, dev in enumerate([716, 30, 90, 150, 20, 80]):          # a check, then two sender runs
        polls.append(ps.compact(1000.0 + i, {"connections": [_conn("ca", "Ann", "Team A", 60 * i, dev)]},
                                None, None))
    ann = ps.sender_summary(polls)[0]
    assert ann["device_hz"] == 46.0, f"(150 + 80) packets in 5 s, not the check's count: {ann}"
    rows = ps.hz_by_minute(polls)[ps.tag("ca")]["rows"]
    assert rows[0]["device_hz"] == 46.0, rows


def t_tables_split_by_block_marks_and_observations_stay_in_the_timeline():
    marks = [{"t": 1000.0, "text": "block B online lobby"}, {"t": 1030.0, "text": "position check 1"},
             {"t": 1065.0, "text": "block C sport"}]
    a = ps.analyse(_polls(), marks)
    assert [b["block"] for b in a["blocks"]] == ["block B online lobby", "block C sport"], a["blocks"]
    first, second = a["blocks"]
    assert first["end"] < 1065.0 <= second["start"], (first["start"], first["end"], second["start"])
    a_rows = {r["entry"]: r for r in first["positions"]}
    assert a_rows["Team A"]["polls"] == 7, f"block B holds the polls up to block C only: {a_rows}"
    text = ps.report_md(a)
    assert "### block B online lobby" in text and "### block C sport" in text, text
    assert "### position check 1" not in text, "an observation is no block"


def t_a_new_session_writes_the_best_it_starts_with():
    polls = []
    for i, (lap, best, last) in enumerate([(3, 90000, 90000), (4, 90000, 91000), (0, 90000, 91000),
                                           (1, 90000, -1)]):
        polls.append(ps.compact(1000.0 + i, {"connections": [_conn("ca", "Ann", "Team A", 60 * i, 60 * i,
                     latest={"lap": lap, "best_ms": best, "last_ms": last})]}, None, None))
    rows = ps.best_laps(polls)[ps.tag("ca")]["rows"]
    assert [r["event"] for r in rows] == ["best changed", "session start"], rows
    assert rows[1]["best_ms"] == 90000 and rows[1]["lap"] == 0, \
        f"a best that GT7 carries into a new session unchanged still gets its row: {rows}"


def t_the_sender_summary_reports_device_and_relay_rates_and_latency():
    rows = {r["name"]: r for r in ps.sender_summary(_polls())}
    ann = rows["Ann"]
    assert ann["device_hz"] is not None and ann["post_ms_p50"] == {"min": 80, "p50": 80, "max": 80}, ann
    assert rows["Cid"]["lost"] == 0
    assert rows["Cid"]["duplicates"] == 60, f"packets posted twice, from the relay's count: {rows['Cid']}"


def t_the_monitor_writes_a_record_per_poll_and_a_probe_snapshot():
    answers = {"/drivers/data": {"connections": [_conn("ca", "Ann", "Team A", 60, 60)]},
               "/drivers/race": _race([_row("Team A", "ca", "Ann", 10.0, 0)]),
               "/telemetry/probe": {"key": "PROBEKEY", "senders": {"Spec": {"packets": {"received": 1},
                                                                               "samples": []}}}}
    clock = [1000.0]
    said = []
    with tempfile.TemporaryDirectory() as d:
        mon = ps.Monitor(d, answers.get, clock=lambda: clock[0], say=said.append)
        mon.poll()
        clock[0] += 1
        answers["/drivers/data"]["connections"][0]["packets"]["received"] = 120
        answers["/drivers/data"]["connections"][0]["sender_stats"]["received"] = 120
        mon.poll()
        polls, marks = ps.load(d)
        snaps = os.listdir(os.path.join(d, ps.PROBE_DIR))
        with open(os.path.join(d, ps.PROBE_DIR, snaps[0]), encoding="utf-8") as fh:
            blob = fh.read()
    assert len(polls) == 2 and marks == [], (polls, marks)
    assert len(snaps) == 1 and "PROBEKEY" not in blob, "one probe snapshot a minute, without the key"
    assert any(line.startswith("Ann") for line in said), said


def t_the_monitor_writes_pseudonyms_and_keeps_the_names_private():
    answers = {"/drivers/data": {"connections": [_conn("ca", "Ann Real", "Team A", 60, 60)]},
               "/drivers/race": dict(_race([_row("Team A", "ca", "Ann Real", 10.0, 0)]),
                                     fastest={"entry": "Team A", "driver": "Ann Real", "ms": 90000})}
    said = []
    with tempfile.TemporaryDirectory() as d:
        mon = ps.Monitor(d, answers.get, clock=lambda: 1000.0, say=said.append)
        mon.poll()
        with open(os.path.join(d, ps.POLL_FILE), encoding="utf-8") as fh:
            blob = fh.read()
        with open(os.path.join(d, "private", "names.json"), encoding="utf-8") as fh:
            names = json.load(fh)
        again = ps.Monitor(d, answers.get, clock=lambda: 1001.0, say=said.append)
        again.poll()
        polls, _m = ps.load(d)
    assert "Ann Real" not in blob, blob
    assert names == {"Driver 1": "Ann Real"}, names
    assert polls[1]["conns"][0]["name"] == polls[1]["race"]["entries"][0]["driver"] == "Driver 1", \
        "a restarted watch keeps the pseudonyms"
    assert any(line.startswith("Ann Real") for line in said), "the producer's screen shows the names"


def t_live_lines_show_the_rate_since_the_last_poll():
    prev = ps.compact(10.0, {"connections": [_conn("ca", "Ann", "Team A", 0, 0)]}, None, None)
    cur = ps.compact(12.0, {"connections": [_conn("ca", "Ann", "Team A", 120, 118,
                                                  latest={"lap": 3, "race_position": 4, "car_count": 9})]},
                     _race([_row("Team A", "ca", "Ann", 1234.0, 3)]), None)
    line = [x for x in ps.live_lines(prev, cur) if x.startswith("Ann")][0]
    assert "60.0" in line and "59.0" in line and "4/9" in line and "1234" in line and "80/210" in line, line
    assert line.split()[7] == "1", f"the duplicates column after lost and reordered: {line}"


def t_marks_split_the_report_into_blocks():
    with tempfile.TemporaryDirectory() as d:
        ps.mark(d, "block 2 sport lobby", clock=lambda: 1000.0)
        _polls_, marks = ps.load(d)
    assert marks == [{"t": 1000.0, "text": "block 2 sport lobby"}]
    a = ps.analyse(_polls(), marks)
    assert "block 2 sport lobby" in ps.report_md(a)
    f = ps.facts(a)
    assert f["changes"] == 1 and f["anchors_carried"] == 1 and f["windows"] == 1 and f["laps_back"] == 1, f


def t_the_cli_writes_the_kit_marks_and_the_report():
    import importlib.util
    spec = importlib.util.spec_from_file_location("probe_stage2_cli", os.path.join(ROOT, "tools", "probe-stage2.py"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with tempfile.TemporaryDirectory() as d:
        kit_dir = os.path.join(d, "kit")
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            fetch = {"/drivers/invite": {"active": True, "id": "INVITEID", "base": "https://host.example"}}.get
            assert cli.main(["kit", "--out", kit_dir], fetch=fetch) == 0
            assert cli.main(["mark", "--out", d, "block 1 start"]) == 0
            with open(os.path.join(d, ps.POLL_FILE), "w", encoding="utf-8") as fh:
                for rec in _polls():
                    fh.write(json.dumps(rec) + "\n")
            assert cli.main(["report", d]) == 0
            assert cli.main(["kit", "--out", kit_dir], fetch={"/drivers/invite": {"active": False}}.get) == 1
        finally:
            sys.stdout = old
        sheets = sorted(os.listdir(kit_dir))
        with open(os.path.join(d, "report.md"), encoding="utf-8") as fh:
            report = fh.read()
        with open(os.path.join(d, "analysis.json"), encoding="utf-8") as fh:
            analysis = json.load(fh)
    assert sheets == sorted(p + ".md" for p in ps.PLATFORMS), sheets
    assert "## Driver changes" in report and "block 1 start" in report, report
    assert analysis["facts"]["changes"] == 1, analysis["facts"]
    assert "invite link" in out.getvalue() and "event start` creates it" in out.getvalue(), out.getvalue()


def _cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("probe_stage2_cli", os.path.join(ROOT, "tools", "probe-stage2.py"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def t_race_reset_posts_the_reset_and_marks_it():
    cli, posted = _cli(), []
    with tempfile.TemporaryDirectory() as d:
        out = io.StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            code = cli.main(["race-reset", "--out", d], fetch=lambda p: None,
                            post=lambda path, body: posted.append((path, body)) or (200, {"ok": True}))
            refused = cli.main(["race-reset", "--out", d], fetch=lambda p: None,
                               post=lambda path, body: (403, {"error": "forbidden"}))
        finally:
            sys.stdout = old
        _polls, marks = ps.load(d)
    assert code == 0 and posted == [("/drivers/race/reset", {})], posted
    assert refused == 1 and "forbidden" in out.getvalue(), out.getvalue()
    assert [m["text"] for m in marks] == ["race reset"], "only a done reset is marked"


def t_collect_keeps_the_audit_log_and_the_recording_apart_as_private():
    cli = _cli()
    with tempfile.TemporaryDirectory() as d:
        runtime, out_dir = os.path.join(d, "runtime"), os.path.join(d, "results")
        os.makedirs(runtime)
        for name in ("driver-race-audit.jsonl", "race-recording-1.sqlite", "race-recording-1.sqlite-wal"):
            with open(os.path.join(runtime, name), "w", encoding="utf-8") as fh:
                fh.write("x")
        sink = io.StringIO()
        old, sys.stdout = sys.stdout, sink
        try:
            assert cli.main(["collect", "--out", out_dir, "--runtime", runtime], fetch=lambda p: None,
                            get_bytes=lambda path: (_ for _ in ()).throw(OSError("relay stopped"))) == 0
        finally:
            sys.stdout = old
        private = sorted(os.listdir(os.path.join(out_dir, "private")))
        top = sorted(os.listdir(out_dir))
    assert private == ["driver-race-audit.jsonl", "race-recording-1.sqlite", "race-recording-1.sqlite-wal"], private
    assert top == ["private"], f"nothing private lands beside the shareable files: {top}"
    assert "relay stopped" in sink.getvalue() and "private" in sink.getvalue(), sink.getvalue()


def t_collect_keeps_the_race_recording_with_its_names_private():
    cli = _cli()
    with tempfile.TemporaryDirectory() as d:
        sink = io.StringIO()
        old, sys.stdout = sys.stdout, sink
        try:
            assert cli.main(["collect", "--out", d], fetch=lambda p: None,
                            get_bytes=lambda path: b"time_utc,entry,driver\n") == 0
        finally:
            sys.stdout = old
        assert os.listdir(d) == ["private"] and os.listdir(os.path.join(d, "private")) == ["race-recording.csv"]


def t_a_saved_check_hides_addresses_and_codes():
    cli = _cli()
    with tempfile.TemporaryDirectory() as d:
        raw = os.path.join(d, "raw.txt")
        with open(raw, "w", encoding="utf-8") as fh:
            fh.write("python3 racecast-sender.py --url https://h/console/drive/ingest --code D1:abc.def --check\n"
                     "12:00:00 check 1/3 heartbeat: OK 2 sent to 192.168.178.58\n"
                     "12:00:00 check 2/3 receive: OK 716 packets from 10.0.0.7, 59.7/s, 296 bytes\n"
                     "12:00:00 RESULT: OK, ready for the session\n")
        sink = io.StringIO()
        old, sys.stdout = sys.stdout, sink
        try:
            assert cli.main(["save-check", "--out", d, "Ann Android", raw], fetch=lambda p: None) == 0
        finally:
            sys.stdout = old
        with open(os.path.join(d, "checks", "Ann Android.txt"), encoding="utf-8") as fh:
            text = fh.read()
    for secret in ("192.168.178.58", "10.0.0.7", "D1:abc.def"):
        assert secret not in text, f"{secret} must not reach the results: {text}"
    assert "sent to <LAN address>" in text and "RESULT: OK" in text, text


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

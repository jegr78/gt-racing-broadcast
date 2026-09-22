#!/usr/bin/env python3
"""Integration checks for the `racecast report` CLI (generate + send)."""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load("racecast", ("src", "racecast.py"))
hs = _load("health_store", ("src", "scripts", "health_store.py"))


def _seed_db(path):
    conn = hs.open_db(path)
    hs.migrate(conn)
    now = 1_700_000_000.0
    for i in range(4):
        hs.record(conn, {"ts": now + i * 30, "health_level": "green",
                         "feed_a_down": 0, "feed_b_down": 0, "live_stint": 1,
                         "health_reasons": []}, "periodic")
    conn.close()


def t_generate_writes_file(monkeypatch=None):
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "health-history.db")
        _seed_db(db)
        reports = os.path.join(d, "reports")
        orig_db, orig_dir = rc._health_db_path, rc._runtime_dir
        orig_map, orig_title = rc._report_name_map, rc._report_event_title
        rc._health_db_path = lambda: db
        rc._runtime_dir = lambda: d
        rc._report_name_map = lambda: {1: "Alice"}
        rc._report_event_title = lambda: "Unit Event"
        try:
            rc.report_cmd(["generate"])
        finally:
            rc._health_db_path, rc._runtime_dir = orig_db, orig_dir
            rc._report_name_map, rc._report_event_title = orig_map, orig_title
        files = os.listdir(reports)
        assert files and files[0].endswith(".html"), files
        with open(os.path.join(reports, files[0]), encoding="utf-8") as fh:
            html = fh.read()
        assert "Unit Event" in html and "Alice" in html


def t_report_backlog_thresholds_follow_the_machine_env():
    # The report counts "behind live" with the relay's own reserve and threshold. (#586)
    orig = rc._machine_env_value
    vals = {"RACECAST_FEED_PREBUFFER_S": "4", "RACECAST_FEED_BACKLOG_WARN_S": "9"}
    rc._machine_env_value = lambda k: vals.get(k, "")
    try:
        assert rc._report_backlog_thresholds() == {"prebuffer_s": 4.0, "backlog_warn_s": 9.0}
        vals.clear()
        assert rc._report_backlog_thresholds() == {"prebuffer_s": 3.0, "backlog_warn_s": 5.0}
    finally:
        rc._machine_env_value = orig


def t_generate_leads_with_the_backlog_finding():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "health-history.db")
        conn = hs.open_db(db)
        hs.migrate(conn)
        now = 1_700_000_000.0
        for i in range(4):
            hs.record(conn, {"ts": now + i * 30, "health_level": "yellow", "live_stint": 1,
                             "live_feed": "A", "feed_a_backlog_s": 19.0, "obs_fps": 45.8,
                             "obs_fps_target": 60.0, "health_reasons": []}, "periodic")
        conn.close()
        orig = (rc._health_db_path, rc._runtime_dir, rc._report_name_map,
                rc._report_event_title, rc._machine_env_value)
        rc._health_db_path = lambda: db
        rc._runtime_dir = lambda: d
        rc._report_name_map = lambda: {}
        rc._report_event_title = lambda: "Unit Event"
        rc._machine_env_value = lambda k: ""
        try:
            r = rc._build_report_file()
        finally:
            (rc._health_db_path, rc._runtime_dir, rc._report_name_map,
             rc._report_event_title, rc._machine_env_value) = orig
        assert r["report"]["finding"]["headline"].startswith("Output ran behind live for 1m 30s")
        assert "fell behind real time" in r["summary"]
        assert "45.8 of 60" in r["html"]


def t_generate_no_data_exits():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "health-history.db")
        conn = hs.open_db(db); hs.migrate(conn); conn.close()   # empty DB
        orig_db, orig_dir = rc._health_db_path, rc._runtime_dir
        rc._health_db_path = lambda: db
        rc._runtime_dir = lambda: d
        try:
            raised = False
            try:
                rc.report_cmd(["generate"])
            except SystemExit:
                raised = True
            assert raised, "expected SystemExit on empty DB"
        finally:
            rc._health_db_path, rc._runtime_dir = orig_db, orig_dir


def t_send_no_webhook_exits():
    with tempfile.TemporaryDirectory() as d:
        reports = os.path.join(d, "reports")
        os.makedirs(reports)
        p = os.path.join(reports, "2026-07-01-x.html")
        with open(p, "w") as fh:
            fh.write("<!doctype html><html></html>")
        orig_dir, orig_hook = rc._runtime_dir, rc._active_discord_webhook
        rc._runtime_dir = lambda: d
        rc._active_discord_webhook = lambda: ("", "")
        try:
            raised = False
            try:
                rc.report_cmd(["send"])
            except SystemExit:
                raised = True
            assert raised, "expected SystemExit when no webhook configured"
        finally:
            rc._runtime_dir, rc._active_discord_webhook = orig_dir, orig_hook


def t_send_posts_multipart():
    with tempfile.TemporaryDirectory() as d:
        reports = os.path.join(d, "reports")
        os.makedirs(reports)
        p = os.path.join(reports, "2026-07-01-x.html")
        with open(p, "w") as fh:
            fh.write("<!doctype html><html>hi</html>")
        sent = {}

        def _fake_post(url, fields=None, files=None, **kw):
            sent["url"] = url
            sent["fields"] = fields
            sent["files"] = files
            return b"ok"

        orig_dir = rc._runtime_dir
        orig_hook = rc._active_discord_webhook
        orig_post = rc.http_util.post_multipart
        rc._runtime_dir = lambda: d
        rc._active_discord_webhook = lambda: ("https://discord.invalid/webhook", "My League")
        rc.http_util.post_multipart = _fake_post
        try:
            rc.report_cmd(["send"])
        finally:
            rc._runtime_dir = orig_dir
            rc._active_discord_webhook = orig_hook
            rc.http_util.post_multipart = orig_post
        assert sent["url"] == "https://discord.invalid/webhook"
        assert "payload_json" in sent["fields"]
        assert sent["files"][0][1] == "2026-07-01-x.zip"
        assert sent["files"][0][3] == "application/zip"


def t_send_report_embed_zip():
    import io
    import json
    import zipfile

    with tempfile.TemporaryDirectory() as d:
        reports = os.path.join(d, "reports")
        os.makedirs(reports)
        p = os.path.join(reports, "2026-07-01-x.html")
        with open(p, "w") as fh:
            fh.write("<!doctype html><html>hi</html>")
        captured = {}

        def _fake_post(url, fields=None, files=None, **kw):
            captured["fields"] = fields
            captured["files"] = files
            return b"ok"

        orig_hook = rc._active_discord_webhook
        orig_post = rc.http_util.post_multipart
        rc._active_discord_webhook = lambda: ("https://discord.invalid/webhook", "My League")
        rc.http_util.post_multipart = _fake_post
        try:
            rc._send_report_core(p, report={"header": {"uptime_pct": 99.0, "on_air_s": 60,
                "duration_s": 60, "start": 0, "end": 60}, "incidents": []})
        finally:
            rc._active_discord_webhook = orig_hook
            rc.http_util.post_multipart = orig_post

        payload = json.loads(captured["fields"]["payload_json"])
        assert payload["username"] == "GT Racecast"
        assert payload["embeds"][0]["fields"][0]["name"] == "Uptime"
        assert "description" not in payload["embeds"][0]     # no finding in this report
        fname, content, ctype = (captured["files"][0][1], captured["files"][0][2],
                                  captured["files"][0][3])
        assert fname.endswith(".zip") and ctype == "application/zip"
        names = zipfile.ZipFile(io.BytesIO(content)).namelist()
        assert any(n.endswith(".html") for n in names)


def t_send_report_embed_leads_with_the_finding():
    import json
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.html")
        with open(p, "w") as fh:
            fh.write("<!doctype html><html>hi</html>")
        captured = {}
        orig_hook, orig_post = rc._active_discord_webhook, rc.http_util.post_multipart
        rc._active_discord_webhook = lambda: ("https://discord.invalid/webhook", "L")
        rc.http_util.post_multipart = lambda url, fields=None, files=None, **kw: \
            captured.update(fields=fields)
        try:
            rc._send_report_core(p, report={
                "header": {"uptime_pct": 99.0, "on_air_s": 60, "duration_s": 60,
                           "start": 0, "end": 60}, "incidents": [],
                "finding": {"headline": "Output ran behind live for 41m of 56m."}})
        finally:
            rc._active_discord_webhook, rc.http_util.post_multipart = orig_hook, orig_post
        embed = json.loads(captured["fields"]["payload_json"])["embeds"][0]
        assert embed.get("description") == "Output ran behind live for 41m of 56m."


def t_send_bundles_sliced_logs_and_host():
    import io
    import json
    import time
    import zipfile

    def clk(s):
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))

    with tempfile.TemporaryDirectory() as d:
        reports = os.path.join(d, "reports")
        os.makedirs(reports)
        p = os.path.join(reports, "2026-07-05-x.html")
        with open(p, "w") as fh:
            fh.write("<!doctype html><html>hi</html>")
        # a racecast-native log (relay, sliceable) with in- and out-of-window lines...
        relay_log = os.path.join(d, "relay.console.log")
        with open(relay_log, "w") as fh:
            fh.write("2026-07-05 21:00:00 INFO way before the event\n"
                     "2026-07-05 21:24:40 INFO inside the session\n"
                     "2026-07-05 23:00:00 INFO long after the event\n")
        # ...and a foreign-format log (obs, time-only prefix) that must be kept whole
        obs_log = os.path.join(d, "obs.log")
        with open(obs_log, "w") as fh:
            fh.write("21:00:00.000: obs boot line\n21:24:40.500: obs mid line\n")
        captured = {}

        def _fake_post(url, fields=None, files=None, **kw):
            captured["fields"] = fields
            captured["files"] = files
            return b"ok"

        orig = (rc._active_discord_webhook, rc.http_util.post_multipart,
                rc._log_sources, rc._report_host)
        rc._active_discord_webhook = lambda: ("https://discord.invalid/webhook", "L")
        rc.http_util.post_multipart = _fake_post
        # all six sources are consulted; provide relay + obs, leave the rest empty
        rc._log_sources = lambda: {"relay": {"files": lambda: [relay_log]},
                                   "obs": {"files": lambda: [obs_log]}}
        rc._report_host = lambda: "STREAM-BOX"
        try:
            rc._send_report_core(p, report={"header": {"uptime_pct": 99.0, "on_air_s": 60,
                "duration_s": 60, "start": 0, "end": 60, "host": "STREAM-BOX"},
                "incidents": []},
                window=(clk("2026-07-05 21:24:32"), clk("2026-07-05 21:30:11")))
        finally:
            (rc._active_discord_webhook, rc.http_util.post_multipart,
             rc._log_sources, rc._report_host) = orig

        payload = json.loads(captured["fields"]["payload_json"])
        assert payload["embeds"][0]["footer"]["text"] == "Produced on STREAM-BOX", payload
        content = captured["files"][0][2]
        zf = zipfile.ZipFile(io.BytesIO(content))
        names = zf.namelist()
        # laid out per source (relay & streams share feed_*.log basenames otherwise)
        assert "logs/relay/relay.console.log" in names, names
        assert "logs/obs/obs.log" in names, names
        relay = zf.read("logs/relay/relay.console.log").decode()
        assert "inside the session" in relay
        assert "way before the event" not in relay
        assert "long after the event" not in relay
        assert "obs boot line" in zf.read("logs/obs/obs.log").decode(), \
            "foreign-format obs log kept whole (not emptied by the window clip)"


def t_report_includes_teardown_events_after_last_sample():
    # part_end and obs_stream_stop are recorded during teardown, a few seconds AFTER
    # the last health sample. The report's event query must extend past the last
    # sample (to + session gap), or they vanish from the Broadcast timeline. (#523)
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "health-history.db")
        conn = hs.open_db(db)
        hs.migrate(conn)
        base = 1_700_000_000.0
        for i in range(4):
            hs.record(conn, {"ts": base + i * 30, "health_level": "green",
                             "feed_a_down": 0, "feed_b_down": 0, "live_stint": 1,
                             "health_reasons": []}, "periodic")
        last_sample = base + 90
        hs.record_event(conn, base, "part_start", label="Q started",
                        metadata={"index": 1})
        # 4 s after the last sample, the teardown tail the old window dropped.
        hs.record_event(conn, last_sample + 4, "part_end", label="Q ended",
                        metadata={"index": 1})
        conn.close()
        reports = os.path.join(d, "reports")
        orig = (rc._health_db_path, rc._runtime_dir,
                rc._report_name_map, rc._report_event_title)
        rc._health_db_path = lambda: db
        rc._runtime_dir = lambda: d
        rc._report_name_map = lambda: {1: "Alice"}
        rc._report_event_title = lambda: "Q Event"
        try:
            rc.report_cmd(["generate"])
        finally:
            (rc._health_db_path, rc._runtime_dir,
             rc._report_name_map, rc._report_event_title) = orig
        with open(os.path.join(reports, os.listdir(reports)[0]),
                  encoding="utf-8") as fh:
            html = fh.read()
        assert "Q ended" in html, \
            "part_end recorded after the last sample must still appear in the timeline"


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")


if __name__ == "__main__":
    run()

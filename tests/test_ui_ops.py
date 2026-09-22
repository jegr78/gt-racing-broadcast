#!/usr/bin/env python3
"""Stdlib checks for the Control Center's structured status providers in racecast.py.
Run: python3 tests/test_ui_ops.py"""
import os, sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
import racecast as rc
sys.path.insert(0, os.path.join(ROOT, "src", "ui"))
import ui_ops


def t_relay_status_data_running():
    d = rc.relay_status_data(read_pid=lambda p: 4242,
                              alive=lambda pid: True,
                              http_ok=lambda: True)
    assert d == {"pid": 4242, "alive": True, "port": 8088, "http_ok": True}


def t_relay_status_data_stopped_skips_http_probe():
    probed = []
    d = rc.relay_status_data(read_pid=lambda p: None,
                              alive=lambda pid: False,
                              http_ok=lambda: probed.append(1) or True)
    assert d == {"pid": None, "alive": False, "port": 8088, "http_ok": False}
    assert probed == []   # never probe HTTP for a dead relay


def t_relay_extra_text_ok_with_tailscale():
    d = {"pid": 1, "alive": True, "port": 8088, "http_ok": True}
    text = rc._relay_extra_text(d, "100.64.0.7")
    assert "control http://127.0.0.1:8088/status OK" in text
    assert "tablet/panel http://100.64.0.7:8088/panel" in text


def t_relay_extra_text_port_down_no_tailscale():
    d = {"pid": 1, "alive": True, "port": 8088, "http_ok": False}
    text = rc._relay_extra_text(d, None)
    assert text == "(port 8088 not responding)"


def t_relay_extra_text_ok_no_tailscale():
    d = {"pid": 1, "alive": True, "port": 8088, "http_ok": True}
    assert rc._relay_extra_text(d, None) == "control http://127.0.0.1:8088/status OK"


def t_companion_payload_running_with_config():
    d = rc.companion_status_payload(True, True,
                                     {"bind_ip": "100.64.0.7", "http_port": 8000})
    assert d == {"supported": True, "running": True,
                 "url": "http://100.64.0.7:8000/tablet", "why": ""}


def t_companion_payload_running_no_config():
    d = rc.companion_status_payload(True, True, None)
    assert d["running"] is True and d["url"] is None


def t_companion_payload_unsupported():
    d = rc.companion_status_payload(False, False, None, "(manual on linux)")
    assert d == {"supported": False, "running": False, "url": None,
                 "why": "(manual on linux)"}


def t_streams_status_data_labels(tmp):
    p1 = os.path.join(tmp, "feed_53001.pid")
    with open(p1, "w") as fh:
        fh.write(str(os.getpid()))          # a live PID -> alive True
    p2 = os.path.join(tmp, "feed_53002.pid")
    with open(p2, "w") as fh:
        fh.write("garbage")                 # unreadable -> pid None, alive False
    feeds = rc.streams_status_data(pidfiles=[p1, p2])
    assert feeds == [
        {"label": "53001", "pid": os.getpid(), "alive": True},
        {"label": "53002", "pid": None, "alive": False}]


def t_streams_status_data_empty():
    assert rc.streams_status_data(pidfiles=[]) == []


def t_ui_status_payload_shape():
    payload = rc.ui_status_payload(
        relay=lambda: {"alive": False}, companion=lambda: {"running": False},
        streams=lambda: [], tailscale=lambda: None,
        cookies=lambda: {"level": "WARN", "detail": "x"},
        apps_running=lambda: {"obs": False, "discord": False})
    assert payload == {"version": rc.version(), "os": rc.sys.platform,
                       "relay": {"alive": False},
                       "companion": {"running": False}, "streams": [],
                       "tailscale_ip": None,
                       "cookies": {"level": "WARN", "detail": "x"},
                       "apps_running": {"obs": False, "discord": False}}


def t_running_apps_data_probe():
    d = rc.running_apps_data(probe=lambda app: app == "obs")
    assert d == {"obs": True, "discord": False}


def t_running_apps_data_never_raises():
    def boom(app):
        raise RuntimeError("pgrep broke")
    d = rc.running_apps_data(probe=boom)
    assert d == {"obs": False, "discord": False}


def t_ops_registry_shape():
    assert ui_ops.OPS["relay-start"] == ["relay", "start"]
    assert ui_ops.OPS["obs-refresh"] == ["obs", "refresh"]
    for name, argv in ui_ops.OPS.items():
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv), name


def t_job_argv_repo_mode():
    argv = ui_ops.job_argv(["relay", "start"], frozen=False,
                           executable="/usr/bin/python3", rc_script="/repo/src/racecast.py")
    assert argv == ["/usr/bin/python3", "/repo/src/racecast.py", "relay", "start"]


def t_job_argv_frozen_reinvokes_binary():
    argv = ui_ops.job_argv(["relay", "stop"], frozen=True,
                           executable="/opt/racecast/racecast", rc_script="/ignored")
    assert argv == ["/opt/racecast/racecast", "relay", "stop"]


def t_ops_registry_routes_in_rc():
    # every registry entry must be a valid racecast invocation (service verb,
    # oneshot, export or command group); route() raises ValueError on anything else
    for name, argv in ui_ops.OPS.items():
        action = rc.route(list(argv))
        assert action["kind"] in ("service", "oneshot", "export", "chat", "discord",
                                  "freeport", "health"), name


def t_build_argv_plain_and_unknown():
    assert ui_ops.build_argv("relay-start") == ["relay", "start"]
    try:
        ui_ops.build_argv("not-an-op")
        raise AssertionError("unknown op accepted")
    except ValueError:
        pass


def t_build_argv_cookies_browser():
    assert ui_ops.build_argv("cookies", {"browser": "firefox"}) == ["cookies", "firefox"]
    assert ui_ops.build_argv("cookies") == ["cookies"]        # browser optional
    try:
        ui_ops.build_argv("cookies", {"browser": "lynx; rm -rf /"})
        raise AssertionError("invalid browser accepted")
    except ValueError:
        pass


def t_build_argv_event_stint():
    assert ui_ops.build_argv("event-start", {"stint": "4"}) == \
        ["event", "start", "--stint", "4"]
    assert ui_ops.build_argv("event-start", {"stint": ""}) == ["event", "start"]
    for bad in ("0", "-1", "abc", "1.5", "٤", "²"):
        try:
            ui_ops.build_argv("event-start", {"stint": bad})
            raise AssertionError(f"invalid stint accepted: {bad}")
        except ValueError:
            pass


def t_event_start_qualifying_flag():
    assert ui_ops.build_argv("event-start", {"qualifying": True}) == ["event", "start", "--qualifying"]
    # composes with stint; falsy/absent omits the flag
    argv = ui_ops.build_argv("event-start", {"stint": "3", "qualifying": True})
    assert "--stint" in argv and "3" in argv and "--qualifying" in argv
    assert ui_ops.build_argv("event-start", {"qualifying": False}) == ["event", "start"]
    assert ui_ops.build_argv("event-start", {}) == ["event", "start"]


def t_build_argv_event_takeover():
    assert ui_ops.build_argv("event-takeover", {"ip": "100.64.1.2"}) == \
        ["event", "takeover", "100.64.1.2"]
    # ip then --stint (order matters: ip is positional)
    assert ui_ops.build_argv("event-takeover", {"ip": "host-b", "stint": "6"}) == \
        ["event", "takeover", "host-b", "--stint", "6"]
    for bad in ("1.2.3.4; rm -rf /", "a b", "$(x)", "ip|cmd"):
        try:
            ui_ops.build_argv("event-takeover", {"ip": bad})
            raise AssertionError(f"invalid host accepted: {bad}")
        except ValueError:
            pass


def t_build_argv_event_takeover_funnel():
    # the --funnel flag rides through to the CLI; the per-league CONSOLE_SECRET is
    # read server-side from the active profile, never sent from the UI
    assert ui_ops.build_argv("event-takeover", {"ip": "host-a.tail.ts.net", "funnel": True}) == \
        ["event", "takeover", "host-a.tail.ts.net", "--funnel"]
    # funnel + stint: host positional first, then the two flags
    assert ui_ops.build_argv(
        "event-takeover", {"ip": "host-a.tail.ts.net", "funnel": True, "stint": "6"}) == \
        ["event", "takeover", "host-a.tail.ts.net", "--funnel", "--stint", "6"]
    # falsy funnel = tailnet takeover, no flag
    assert ui_ops.build_argv("event-takeover", {"ip": "100.64.1.2", "funnel": False}) == \
        ["event", "takeover", "100.64.1.2"]
    # the MagicDNS host is charset-locked by the same validator as the tailnet IP
    for bad in ("a.b; rm -rf /", "host a", "$(x)"):
        try:
            ui_ops.build_argv("event-takeover", {"ip": bad, "funnel": True})
            raise AssertionError(f"invalid funnel host accepted: {bad}")
        except ValueError:
            pass


def t_build_argv_update_flag():
    assert ui_ops.build_argv("install-tools", {"update": True}) == \
        ["install-tools", "--yes", "--update"]
    assert ui_ops.build_argv("install-tools", {"update": False}) == \
        ["install-tools", "--yes"]
    assert ui_ops.build_argv("install-apps", {"update": True}) == \
        ["install-apps", "--yes", "--update"]
    assert ui_ops.build_argv("install-apps", {"update": False}) == \
        ["install-apps", "--yes"]


def t_build_argv_update():
    # Regular update: no tag -> goes to the latest release.
    assert ui_ops.build_argv("update") == ["update", "--yes"]


def t_build_argv_update_with_preview_tag():
    # A preview install is the same op with a tag param, so one op name lets the job
    # manager serialise it against a concurrent regular update.
    assert ui_ops.build_argv("update", {"tag": "preview-pr-42"}) == \
        ["update", "--yes", "--tag", "preview-pr-42"]
    assert ui_ops.build_argv("update", {"tag": "preview-main"}) == \
        ["update", "--yes", "--tag", "preview-main"]


def t_build_argv_update_empty_tag_omits_flag():
    # blank tag is "not provided" -> no --tag appended (build_argv contract)
    assert ui_ops.build_argv("update", {"tag": ""}) == ["update", "--yes"]


def t_build_argv_update_rejects_non_preview_tag():
    # The UI op installs preview tags only: stable v-tags are a downgrade vector, and
    # junk, shell metacharacters, whitespace and a trailing newline are all rejected.
    for bad in ("v1.2.3", "preview-pr-42; rm -rf /", "../../etc", "weird tag",
                "release", "preview-x\n"):
        try:
            ui_ops.build_argv("update", {"tag": bad})
            raise AssertionError(f"accepted bad tag {bad!r}")
        except ValueError:
            pass


def t_build_argv_rejects_unknown_params():
    try:
        ui_ops.build_argv("relay-start", {"stint": "4"})
        raise AssertionError("param on paramless op accepted")
    except ValueError:
        pass


def t_cookies_status_data_shape():
    class R:
        level, detail = "PASS", "fresh (1 h old)"
    d = rc.cookies_status_data(status=R)
    assert d == {"level": "PASS", "detail": "fresh (1 h old)"}


def t_cookies_status_data_never_raises():
    def boom():
        raise RuntimeError("probe broke")
    d = rc.cookies_status_data(status=boom)
    assert d["level"] == "WARN" and "probe broke" in d["detail"]


def t_assets_status_data_complete(tmp):
    d = rc.assets_status_data(state=lambda ev: (tmp, tmp, [], []))
    assert d["ok"] is True
    assert d["graphics"]["level"] == "PASS" and d["media"]["level"] == "PASS"


def t_assets_status_data_missing_and_unverified(tmp):
    # graphics: sheet readable, one file missing -> FAIL with the filename.
    # media: sheet unreadable (None) plus an empty local dir -> its severity, WARN
    d = rc.assets_status_data(state=lambda ev: (tmp, tmp, ["Overlay.png"], None))
    assert d["graphics"]["level"] == "FAIL"
    assert "Overlay.png" in d["graphics"]["detail"]
    assert d["media"]["level"] == "WARN"


def t_assets_status_data_error():
    def boom(ev):
        raise RuntimeError("no sheet")
    d = rc.assets_status_data(state=boom)
    assert d["ok"] is False and "no sheet" in d["error"]


def t_assets_status_data_refreshes_active_profile_env():
    # The sheet-driven asset check reads RACECAST_SHEET_ID, so a profile changed
    # while the Control Center runs must be re-injected before _asset_state
    # fetches the sheet.
    order = []
    def refresh():
        order.append("refresh")
    def state(ev):
        order.append("state")
        return ("g", "m", [], [])
    rc.assets_status_data(state=state, refresh_env=refresh)
    assert order == ["refresh", "state"]       # env refreshed before the fetch


def t_tools_status_data_mixed():
    d = rc.tools_status_data(
        which=lambda n: "/usr/bin/" + n if n in ("yt-dlp", "ffmpeg") else None,
        version=lambda n: n + " 1.2.3")
    assert d["ok"] is True
    by = {t["name"]: t for t in d["tools"]}
    assert by["yt-dlp"]["installed"] is True and by["yt-dlp"]["version"] == "yt-dlp 1.2.3"
    assert by["streamlink"]["installed"] is False and by["streamlink"]["version"] is None
    assert {t["name"] for t in d["tools"]} >= {"yt-dlp", "streamlink", "ffmpeg", "deno"}, \
        "every canonical tool is represented"


def t_tools_status_data_includes_speedtest():
    # speedtest is a first-class row; version is probed by the RESOLVED path so a
    # managed-dir install (not on PATH) still reports it.
    seen = {}

    def ver(path):
        seen["path"] = path
        return "Speedtest by Ookla 1.2.0"

    d = rc.tools_status_data(
        which=lambda n: "/runtime/bin/speedtest" if n == "speedtest" else None,
        version=ver)
    by = {t["name"]: t for t in d["tools"]}
    assert "speedtest" in by
    assert by["speedtest"]["installed"] is True
    assert by["speedtest"]["version"] == "Speedtest by Ookla 1.2.0"
    assert seen["path"] == "/runtime/bin/speedtest"   # probed by path, not by name


def t_tools_status_data_error():
    def boom(n):
        raise RuntimeError("which broke")
    d = rc.tools_status_data(which=boom)
    assert d["ok"] is False and "which broke" in d["error"]


def t_apps_status_data_shape():
    d = rc.apps_status_data(present=lambda app: app == "obs")
    assert d["ok"] is True
    by = {a["name"]: a["installed"] for a in d["apps"]}
    assert by["obs"] is True and by["companion"] is False
    assert {a["name"] for a in d["apps"]} >= {"obs", "companion", "tailscale", "discord"}


def t_apps_status_data_includes_version():
    # The Control Center renders a version next to each installed app: present apps
    # carry their probed version, absent apps carry None. (#91)
    d = rc.apps_status_data(present=lambda app: app in ("obs", "discord"),
                            version=lambda app: "31.0.2" if app == "obs" else None)
    by = {a["name"]: a for a in d["apps"]}
    assert by["obs"]["version"] == "31.0.2"
    assert by["discord"]["installed"] is True and by["discord"]["version"] is None
    assert by["companion"]["installed"] is False and by["companion"]["version"] is None, \
        "an app that isn't installed is never version-probed"


def t_apps_status_data_error():
    def boom(app):
        raise RuntimeError("probe broke")
    d = rc.apps_status_data(present=boom)
    assert d["ok"] is False and "probe broke" in d["error"]


def t_apps_status_data_caches_companion_version(tmp):
    import json as _json
    cache = os.path.join(tmp, "companion-version.json")
    orig = rc._companion_version_cache_path
    rc._companion_version_cache_path = lambda: cache
    try:
        # Companion present + version probed -> shown and written to the cache.
        d1 = rc.apps_status_data(present=lambda a: a == "companion",
                                 version=lambda a: "4.3.4")
        comp1 = {a["name"]: a for a in d1["apps"]}["companion"]
        assert comp1["version"] == "4.3.4"
        with open(cache, encoding="utf-8") as fh:
            assert _json.load(fh)["version"] == "4.3.4"
        # Companion still present but the probe now fails (server stopped) ->
        # the last-known version is served from the cache.
        d2 = rc.apps_status_data(present=lambda a: a == "companion",
                                 version=lambda a: None)
        comp2 = {a["name"]: a for a in d2["apps"]}["companion"]
        assert comp2["version"] == "4.3.4"
    finally:
        rc._companion_version_cache_path = orig


def t_preflight_data_sections():
    class R:
        def __init__(self, level, name, detail):
            self.level, self.name, self.detail = level, name, detail
    fake = [("Hardware", [R("PASS", "RAM", "32 GB"), R("WARN", "Swap", "in use")]),
            ("Tool chain", [R("FAIL", "ffmpeg", "missing")])]
    d = rc.preflight_data(gather=lambda: fake)
    assert d["ok"] is True
    assert d["sections"][0]["title"] == "Hardware"
    assert d["sections"][0]["results"][0] == {"level": "PASS", "name": "RAM", "detail": "32 GB"}
    assert d["sections"][1]["results"][0]["level"] == "FAIL"


def t_preflight_data_error():
    def boom():
        raise RuntimeError("no preflight")
    d = rc.preflight_data(gather=boom)
    assert d["ok"] is False and "no preflight" in d["error"]


def t_preflight_data_refreshes_active_profile_env():
    # The Control Center holds os.environ for the life of the process, but the
    # active profile can change underneath it. preflight_data must re-inject the
    # active profile's league env before the sheet probe, or it reads a stale
    # RACECAST_SHEET_ID and warns "not set" even though SHEET_ID is configured.
    order = []
    def refresh():
        order.append("refresh")
    def gather():
        order.append("gather")
        return [("Sheet", [])]
    d = rc.preflight_data(gather=gather, refresh_env=refresh)
    assert d["ok"] is True
    assert order == ["refresh", "gather"]      # env refreshed first, then probed


def t_assets_files_data_lists(tmp):
    g = os.path.join(tmp, "graphics")
    m = os.path.join(tmp, "media")
    os.makedirs(g)
    os.makedirs(m)
    open(os.path.join(g, "Overlay.png"), "w").close()
    open(os.path.join(g, "Standings.png"), "w").close()
    open(os.path.join(g, "notes.txt"), "w").close()      # non-image: ignored
    open(os.path.join(m, "intro.mp4"), "w").close()
    d = rc.assets_files_data(roots={"graphics": g, "media": m}, profile="alpha")
    assert d["ok"] is True
    assert d["profile"] == "alpha"
    # entries are {name, v}; names are still sorted with non-image/.txt dropped
    assert [f["name"] for f in d["graphics"]] == ["Overlay.png", "Standings.png"]
    assert [f["name"] for f in d["media"]] == ["intro.mp4"]


def t_assets_files_data_media_includes_audio(tmp):
    # The Intermission Music mp3 lands in runtime/<profile>/media/, so the gallery
    # lists audio as well as video and tags each item kind=audio|video, which is
    # how the Control Center picks an <audio> over a <video> tile. (#398)
    m = os.path.join(tmp, "media398")
    os.makedirs(m)
    open(os.path.join(m, "intro.mp4"), "w").close()
    open(os.path.join(m, "intermission.mp3"), "w").close()
    open(os.path.join(m, "notes.txt"), "w").close()      # non-media: ignored
    d = rc.assets_files_data(roots={"graphics": os.path.join(tmp, "g398"),
                                    "media": m}, profile="alpha")
    assert d["ok"] is True
    media = {f["name"]: f["kind"] for f in d["media"]}
    assert media == {"intermission.mp3": "audio", "intro.mp4": "video"}


def t_assets_files_data_cache_token(tmp):
    # Each file carries a per-profile, per-mtime cache token so the Control Center
    # gallery busts the browser <img> decode-cache on a profile switch or a
    # re-download. The token must change with both the profile and the file's mtime,
    # because profiles usually share filenames with different bytes. (#274)
    g = os.path.join(tmp, "ct_graphics")
    os.makedirs(g)
    p = os.path.join(g, "Overlay.png")
    open(p, "w").close()
    os.utime(p, (1000, 1000))
    roots = {"graphics": g, "media": os.path.join(tmp, "ct_none")}
    a = rc.assets_files_data(roots=roots, profile="alpha")["graphics"][0]
    b = rc.assets_files_data(roots=roots, profile="bravo")["graphics"][0]
    assert a["v"] == "alpha-1000" and b["v"] == "bravo-1000"   # profile busts
    os.utime(p, (2000, 2000))
    c = rc.assets_files_data(roots=roots, profile="alpha")["graphics"][0]
    assert c["v"] == "alpha-2000"                              # mtime busts


def t_assets_files_data_missing_dirs(tmp):
    d = rc.assets_files_data(roots={"graphics": os.path.join(tmp, "nope"),
                                     "media": os.path.join(tmp, "nope2")},
                             profile="x")
    assert d["ok"] is True and d["graphics"] == [] and d["media"] == []


def t_assets_files_data_error():
    d = rc.assets_files_data(roots={}, profile="x")  # missing keys -> KeyError, caught
    assert d["ok"] is False and "error" in d


def t_env_entries_data_reads(tmp):
    p = os.path.join(tmp, ".env")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# comment\nRACECAST_SHEET_ID=abc\nRACECAST_UI_PORT=8090\n")
    d = rc.env_entries_data(path=p)
    assert d["ok"] is True and d["path"] == p
    assert d["entries"] == [{"key": "RACECAST_SHEET_ID", "value": "abc"},
                            {"key": "RACECAST_UI_PORT", "value": "8090"}]


def t_env_entries_data_missing_file(tmp):
    d = rc.env_entries_data(path=os.path.join(tmp, "nope.env"))
    assert d["ok"] is True and d["entries"] == []


def t_env_write_preserves_comments_and_round_trips(tmp):
    p = os.path.join(tmp, ".env")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# header\nRACECAST_SHEET_ID=old\n\n# port note\nRACECAST_UI_PORT=8089\n")
    res = rc.env_write_data([{"key": "RACECAST_SHEET_ID", "value": "new"},
                              {"key": "RACECAST_NEW", "value": "x"}], path=p)
    assert res["ok"] is True
    with open(p, encoding="utf-8") as fh:
        text = fh.read()
    assert "# header" in text and "# port note" in text     # comments kept
    assert "RACECAST_SHEET_ID=new" in text                  # updated in place
    assert "RACECAST_UI_PORT" not in text                   # removed
    assert "RACECAST_NEW=x" in text                          # appended
    back = rc.env_entries_data(path=p)["entries"]
    assert {"key": "RACECAST_SHEET_ID", "value": "new"} in back
    assert {"key": "RACECAST_NEW", "value": "x"} in back


def t_env_write_rejects_non_racecast_key(tmp):
    # The machine .env editor writes only RACECAST_* knobs, so it cannot set a
    # process-loader var such as LD_PRELOAD, DYLD_INSERT_LIBRARIES or PATH that
    # spawned children would inherit. profile.env, edited via
    # profile_env_write_data, still accepts un-prefixed league keys.
    p = os.path.join(tmp, "machine-reject.env")    # unique: shared tmp dir
    res = rc.env_write_data([{"key": "RACECAST_SHEET_ID", "value": "ok"},
                              {"key": "LD_PRELOAD", "value": "/tmp/evil.so"}], path=p)
    assert res["ok"] is False and "RACECAST_" in res["error"]
    assert not os.path.exists(p)            # nothing written on rejection


def t_env_write_rejects_bad_key(tmp):
    res = rc.env_write_data([{"key": "bad key", "value": "x"}],
                             path=os.path.join(tmp, ".env"))
    assert res["ok"] is False and "invalid key" in res["error"]


def t_env_write_rejects_duplicate_and_newline(tmp):
    p = os.path.join(tmp, ".env")
    r1 = rc.env_write_data([{"key": "A", "value": "1"}, {"key": "A", "value": "2"}], path=p)
    assert r1["ok"] is False and "duplicate" in r1["error"]
    r2 = rc.env_write_data([{"key": "A", "value": "a\nb"}], path=p)
    assert r2["ok"] is False and "line break" in r2["error"]


def t_env_write_drops_blank_rows(tmp):
    p = os.path.join(tmp, ".env")
    res = rc.env_write_data([{"key": "", "value": ""},
                              {"key": "RACECAST_A", "value": "1"}], path=p)
    assert res["ok"] is True
    assert rc.env_entries_data(path=p)["entries"] == [{"key": "RACECAST_A", "value": "1"}]


# Relay live stats (Home dashboard).

def t_relay_live_data_safe_subset():
    # The relay /status carries channel and url fields per feed, so relay_live_data
    # must surface only the stint and state, which is safe to screenshot or share.
    def fetch(url):
        if url.endswith("/status"):
            return {"schedule_len": 12,
                    "health": {"level": "red", "reasons": ["Feed A down"], "since_s": 8.0},
                    "feeds": {
                        "A": {"stint": 3, "state": "connecting", "channel": "secret-handle",
                              "index": 2, "port": 53001, "down": True},
                        "B": {"stint": 4, "state": "serving", "channel": "other"}}}
        return {"mode": "running", "visible": True, "end": 1000.0,
                "server_now": 940.0, "remaining_s": None, "duration_s": 3600}
    d = rc.relay_live_data(fetch=fetch, started=lambda: 100.0)
    assert d["ok"] is True and d["schedule_len"] == 12
    assert d["feeds"] == [{"feed": "A", "stint": 3, "state": "connecting", "down": True},
                          {"feed": "B", "stint": 4, "state": "serving", "down": False}]
    assert d["health"] == {"level": "red", "reasons": ["Feed A down"], "since_s": 8.0}
    blob = repr(d)
    assert "channel" not in blob and "secret-handle" not in blob
    assert d["timer"]["mode"] == "running" and d["timer"]["end"] == 1000.0
    assert isinstance(d["uptime_s"], int) and d["uptime_s"] >= 0


def t_relay_live_data_unreachable():
    def boom(url):
        raise OSError("connection refused")
    assert rc.relay_live_data(fetch=boom) == {"ok": False}


def t_relay_live_data_never_raises_on_garbage():
    assert rc.relay_live_data(fetch=lambda url: "not-a-dict") == {"ok": False}


# Self-update check (UI wrapper over scripts/update.py).

def _release(tag, with_asset=True):
    """A GitHub latest-release payload shaped like update.classify expects."""
    rel = {"tag_name": tag, "assets": []}
    if with_asset:
        rel["assets"] = [{"name": "racecast-windows.zip", "browser_download_url": "u"},
                         {"name": "racecast-macos.tar.gz", "browser_download_url": "u"},
                         {"name": "racecast-linux.tar.gz", "browser_download_url": "u"}]
    return rel


def t_update_check_newer_available():
    d = rc.update_check_data(fetch=lambda: _release("v1.3.0"), current="v1.2.0",
                              platform="darwin")
    assert d["ok"] and d["update_available"] is True
    assert d["latest"] == "v1.3.0" and d["current"] == "v1.2.0"
    assert "releases/latest" in d["releases_url"]


def t_update_check_building_counts_as_available():
    # newer tag, platform asset not uploaded yet -> still "update available"
    d = rc.update_check_data(fetch=lambda: _release("v1.3.0", with_asset=False),
                              current="v1.2.0", platform="darwin")
    assert d["ok"] and d["update_available"] is True and d["latest"] == "v1.3.0"


def t_update_check_up_to_date():
    d = rc.update_check_data(fetch=lambda: _release("v1.2.0"), current="v1.2.0",
                              platform="darwin")
    assert d["ok"] and d["update_available"] is False and d["latest"] == "v1.2.0"


def t_update_check_dev_build_skips():
    # running from source with a non-semver version skips, because a repo checkout
    # cannot self-update
    d = rc.update_check_data(fetch=lambda: _release("v9.9.9"), current="dev",
                              frozen=False)
    assert d["ok"] and d["update_available"] is False and d["latest"] is None


def t_update_check_frozen_preview_offers_latest():
    # a frozen preview binary has a non-semver version ('preview-main-<sha>') but is
    # a real installable artifact, so it offers the latest release, matching the
    # CLI's `racecast update --check` (#70)
    d = rc.update_check_data(fetch=lambda: _release("v0.1.0"),
                              current="preview-main-4c25fc8", platform="darwin",
                              frozen=True)
    assert d["ok"] and d["update_available"] is True and d["latest"] == "v0.1.0"


def t_update_check_frozen_dev_offers_latest():
    # a locally built frozen binary (version 'dev', no --version stamp) likewise
    # jumps to the latest release rather than being told to run `git pull`
    d = rc.update_check_data(fetch=lambda: _release("v0.1.0"), current="dev",
                              platform="darwin", frozen=True)
    assert d["ok"] and d["update_available"] is True and d["latest"] == "v0.1.0"


def t_update_check_offline_is_not_ok():
    def boom():
        raise OSError("offline")
    d = rc.update_check_data(fetch=boom, current="v1.2.0")
    assert d["ok"] is False and d["update_available"] is False


def t_update_check_includes_release_notes():
    rel = {"tag_name": "v9.9.9", "body": "## What's new\n- stuff",
           "assets": [{"name": "racecast-macos.tar.gz", "browser_download_url": "https://x/m"}]}
    d = rc.update_check_data(fetch=lambda: rel, current="v1.0.0", platform="darwin")
    # Release notes are GitHub-authored, so untrusted, and shown as plaintext in the
    # dialog: the raw body is returned verbatim with no rendered-HTML field. (#101)
    assert d["ok"] and d["notes"] == "## What's new\n- stuff"
    assert "notes_html" not in d


def t_update_check_notes_carry_no_rendered_html():
    # An untrusted body must never be turned into HTML server-side, because the
    # client renders it via textContent, so a body carrying a script or a javascript
    # link is returned verbatim as plaintext with no notes_html field to inject.
    rel = {"tag_name": "v9.9.9",
           "body": "[x](javascript:alert(1)) <script>alert(2)</script>",
           "assets": [{"name": "racecast-macos.tar.gz", "browser_download_url": "https://x/m"}]}
    d = rc.update_check_data(fetch=lambda: rel, current="v1.0.0", platform="darwin")
    assert "notes_html" not in d
    assert d["notes"] == "[x](javascript:alert(1)) <script>alert(2)</script>"


def t_preview_list_data_notes_are_plaintext():
    releases = [{"tag_name": "preview-pr-7", "prerelease": True, "name": "P7",
                 "target_commitish": "abc1234", "body": "**bold**",
                 "assets": [{"name": "racecast-macos.tar.gz",
                             "browser_download_url": "https://x/p7"}]}]
    d = rc.preview_list_data(fetch=lambda: releases, platform="darwin")
    assert d["previews"][0]["notes"] == "**bold**"
    assert "notes_html" not in d["previews"][0]


def t_preview_list_data_ok():
    releases = [
        {"tag_name": "v1.0.0", "prerelease": False, "assets": []},
        {"tag_name": "preview-pr-7", "prerelease": True, "name": "Preview: PR #7",
         "target_commitish": "abc1234", "published_at": "2026-06-10T00:00:00Z",
         "body": "n", "assets": [{"name": "racecast-macos.tar.gz",
                                  "browser_download_url": "https://x/p7"}]},
    ]
    d = rc.preview_list_data(fetch=lambda: releases, platform="darwin")
    assert d["ok"]
    assert [p["tag"] for p in d["previews"]] == ["preview-pr-7"]
    assert d["previews"][0]["asset_url"] == "https://x/p7"


def t_preview_list_data_offline_returns_not_ok():
    def boom():
        raise OSError("no network")
    d = rc.preview_list_data(fetch=boom, platform="darwin")
    assert d == {"ok": False, "previews": []}


def t_streams_config_defaults_when_absent():
    d = rc.streams_config_data(path="/nope/streams.json",
                                default=lambda: [{"label": "Feed A",
                                                  "channel": "UC1", "port": "53001"}])
    assert d["ok"] and d["entries"][0]["port"] == "53001"


def t_streams_config_round_trip(tmp):
    p = os.path.join(tmp, "streams.json")
    res = rc.streams_config_write_data(
        [{"label": "Feed A", "channel": "UC9", "port": "53001"},
         {"label": "", "channel": "UC8", "port": "53002"}], path=p)
    assert res["ok"] is True
    back = rc.streams_config_data(path=p)["entries"]
    assert [e["channel"] for e in back] == ["UC9", "UC8"]
    assert [e["port"] for e in back] == ["53001", "53002"]


def t_streams_config_validation():
    ok, err = rc._validate_streams_entries(
        [{"channel": "UC1", "port": "53001"}, {"channel": "", "port": ""}])
    assert err is None and len(ok) == 1                 # blank row dropped
    _bad, err = rc._validate_streams_entries([{"channel": "UC1", "port": "x"}])
    assert err and "number" in err
    _bad, err = rc._validate_streams_entries([{"channel": "", "port": "53001"}])
    assert err and "channel" in err
    _bad, err = rc._validate_streams_entries(
        [{"channel": "UC1", "port": "53001"}, {"channel": "UC2", "port": "53001"}])
    assert err and "duplicate" in err


def t_streams_config_write_rejects_bad(tmp):
    p = os.path.join(tmp, "streams_reject.json")
    res = rc.streams_config_write_data([{"channel": "UC1", "port": "x"}], path=p)
    assert res["ok"] is False and "number" in res["error"]
    assert not os.path.exists(p)                        # nothing written on error


def t_docs_data_lists_present_only(tmp):
    # only docs that exist on disk are listed; wiki + onboarding-decks URLs always present
    base = os.path.join(tmp, "docs_data")
    os.makedirs(base, exist_ok=True)
    open(os.path.join(base, "README_SETUP.md"), "w").close()
    def resolve(rel):
        return os.path.join(base, os.path.basename(rel))
    d = rc.docs_data(resolve=resolve)
    keys = [x["key"] for x in d["local"]]
    assert keys == ["setup-readme"]                      # only the md exists
    assert d["local"][0]["kind"] == "markdown"
    assert "/wiki" in d["wiki_url"] and "Director-Setup" in d["director_url"]
    assert urlparse(d["decks_url"]).hostname.endswith(".github.io")  # Pages hub
    assert d["decks_local_url"] is None                  # no bundled slides in this fixture


def t_docs_file_path_allowlist(tmp):
    base = os.path.join(tmp, "docs_path")
    os.makedirs(base, exist_ok=True)
    open(os.path.join(base, "README_SETUP.md"), "w").close()
    def resolve(rel):
        return os.path.join(base, os.path.basename(rel))
    assert rc.docs_file_path("setup-readme", resolve=resolve).endswith(
        "README_SETUP.md")
    assert rc.docs_file_path("setup-guide", resolve=resolve) is None  # not on disk
    assert rc.docs_file_path("cheat-sheet", resolve=resolve) is None  # no longer served locally
    assert rc.docs_file_path("../../etc/passwd", resolve=resolve) is None
    assert rc.docs_file_path("unknown", resolve=resolve) is None


def t_docs_content_md_rendered(tmp):
    base = os.path.join(tmp, "docs_content")
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "README_SETUP.md"), "w") as fh:
        fh.write("# Title\n\n| A | B |\n|--|--|\n| 1 | 2 |\n")
    def resolve(rel):
        return os.path.join(base, os.path.basename(rel))
    # markdown docs are rendered to a styled, self-contained HTML page
    ctype, body = rc.docs_content("setup-readme", resolve=resolve)
    text = body.decode("utf-8")
    assert ctype.startswith("text/html")
    assert "<!doctype html>" in text and "<h1>Title</h1>" in text and "<table>" in text
    assert rc.docs_content("cheat-sheet", resolve=resolve) is None  # not an allowlisted local doc
    assert rc.docs_content("unknown", resolve=resolve) is None


def t_docs_slides_serve_and_local_url(tmp):
    # the bundled offline copy of the onboarding decks serves from src/docs/slides;
    # resolve("docs/slides") -> base/slides in this fixture
    base = os.path.join(tmp, "docs_slides")
    slides = os.path.join(base, "slides")
    os.makedirs(os.path.join(slides, "assets"), exist_ok=True)
    with open(os.path.join(slides, "index.html"), "w") as fh:
        fh.write("<!doctype html><h1>decks</h1>")
    with open(os.path.join(slides, "assets", "deck.css"), "w") as fh:
        fh.write(".reveal{}")
    def resolve(rel):
        return os.path.join(base, os.path.basename(rel))
    # empty path -> index.html; nested asset; content types
    p, ct = rc.docs_slides_serve("", resolve=resolve)
    assert p.endswith("index.html") and ct.startswith("text/html")
    p, ct = rc.docs_slides_serve("assets/deck.css", resolve=resolve)
    assert p.endswith("deck.css") and ct.startswith("text/css")
    # missing + traversal are refused
    assert rc.docs_slides_serve("nope.html", resolve=resolve) is None
    assert rc.docs_slides_serve("../../etc/passwd", resolve=resolve) is None
    # docs_data advertises the offline hub when the bundled index resolves
    assert rc.docs_data(resolve=resolve)["decks_local_url"] == "/docs/slides/"


def t_app_control_ops_route():
    for name in ("obs-start", "obs-stop", "discord-start", "discord-stop",
                 "tailscale-start", "tailscale-stop"):
        assert rc.route(list(ui_ops.OPS[name]))["kind"] == "service"


def t_init_plan_data_shape_and_safety():
    steps = [
        {"key": "profile", "label": "profile (league)",
         "done": lambda: "profile 'demo' ready"},
        {"key": "cookies", "label": "cookies", "done": lambda: None},
        {"key": "preflight", "label": "preflight", "done": lambda: None},
    ]
    out = rc.init_plan_data(steps, rc.ins.STEP_KINDS, browser="chrome",
                             next_steps=["import the OBS collection"])
    assert out["ok"] is True
    by_key = {s["key"]: s for s in out["steps"]}
    assert by_key["profile"]["done"] is True
    assert by_key["profile"]["kind"] == "gate"
    assert by_key["cookies"]["done"] is False
    assert by_key["cookies"]["op"] == "cookies"
    # browser is interpolated into the instruction
    assert "chrome" in by_key["cookies"]["instruction"]
    assert out["next_steps"] == ["import the OBS collection"]


def t_init_plan_data_never_raises_on_probe_error():
    def boom():
        raise RuntimeError("sheet down")
    steps = [{"key": "graphics", "label": "graphics", "done": boom}]
    out = rc.init_plan_data(steps, rc.ins.STEP_KINDS, browser="firefox",
                             next_steps=[])
    assert out["ok"] is True
    assert out["steps"][0]["done"] is False


def t_init_step_action_rejects_job_steps():
    res = rc.init_step_action_data("cookies")
    assert res["ok"] is False
    assert "cookies" in res["error"]


def t_wizard_job_ops_all_exist_in_registry():
    # every kind=job wizard step must name an op the UI can run; a typo here would
    # 404 the wizard's "Run" button at runtime
    job_ops = [m["op"] for m in rc.ins.STEP_KINDS.values()
               if m["kind"] == "job"]
    assert job_ops                      # guard against an empty/renamed table
    for op_name in job_ops:
        assert op_name in ui_ops.OPS, f"wizard op {op_name!r} missing from OPS"


def t_rc_job_executable_frozen_uses_sibling():
    # frozen racecast-ui must spawn the sibling `racecast`, not itself
    posix = rc._rc_job_executable(frozen=True,
                                    executable="/opt/racecast/racecast-ui", win=False)
    assert posix == "/opt/racecast/racecast"
    win = rc._rc_job_executable(frozen=True,
                                  executable="C:\\racecast\\racecast-ui.exe", win=True)
    assert win.endswith("racecast.exe")


def t_rc_job_executable_dev_uses_interpreter():
    # non-frozen: the running interpreter (paired with racecast.py)
    assert rc._rc_job_executable(frozen=False, executable="/usr/bin/python3",
                                   win=False) == "/usr/bin/python3"


def t_app_home_plain_binary_is_dirname():
    assert rc._app_home("/opt/racecast/racecast-ui") == "/opt/racecast"
    assert rc._app_home("/opt/racecast/racecast") == "/opt/racecast"


def t_app_home_macos_app_resolves_next_to_bundle():
    # inside a .app the real home is the folder containing the bundle, where the
    # sibling racecast binary and runtime/.env live, not Contents/MacOS/
    exe = "/Users/x/racecast/racecast-ui.app/Contents/MacOS/racecast-ui"
    assert rc._app_home(exe) == "/Users/x/racecast"


def t_rc_job_executable_macos_app_finds_sibling_next_to_bundle():
    # jobs must target <home>/racecast, not the missing Contents/MacOS/racecast
    # inside the bundle
    exe = "/Users/x/racecast/racecast-ui.app/Contents/MacOS/racecast-ui"
    assert rc._rc_job_executable(frozen=True, executable=exe,
                                   win=False) == "/Users/x/racecast/racecast"


def t_obs_collection_set_op_builds_argv():
    assert ui_ops.build_argv("obs-collection-set") == ["obs", "collection", "set"]


def t_chat_clear_op_builds_argv():
    assert ui_ops.build_argv("chat-clear") == ["chat", "clear"]


def t_kill_relay_op_builds_argv():
    # The "Kill stale relay" button force-frees the relay control port and the feed ports.
    assert ui_ops.OPS["kill-relay"] == [
        "freeport", "--force", "8088", "53001", "53002", "53003"]
    assert ui_ops.build_argv("kill-relay") == [
        "freeport", "--force", "8088", "53001", "53002", "53003"]


def t_kill_relay_op_is_forceful_and_covers_feed_ports():
    # The manual emergency brake must kill the holders: without --force, freeport
    # refuses while a relay's PID file reports "alive", so it never reaches a
    # cross-profile orphan. Cover the control port and the feed ports.
    assert ui_ops.OPS["kill-relay"] == [
        "freeport", "--force", "8088", "53001", "53002", "53003"]


def t_kill_relay_op_routes_to_freeport():
    assert rc.route(list(ui_ops.OPS["kill-relay"]))["kind"] == "freeport"


def t_kill_relay_op_rejects_params():
    try:
        ui_ops.build_argv("kill-relay", {"port": "53001"})
        raise AssertionError("expected ValueError for an unexpected param")
    except ValueError:
        pass


def t_obs_collection_set_op_rejects_params():
    try:
        ui_ops.build_argv("obs-collection-set", {"x": "1"})
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unexpected param")


def t_obs_collection_data_ok_passes_status_through():
    status = {"current": "Other", "expected": "GT Racing Endurance",
              "available": ["GT Racing Endurance", "Other"], "match": False,
              "expected_present": True, "renamed_variant": None}
    d = rc.obs_collection_data(get=lambda: (status, ""))
    assert d["ok"] is True
    assert d["current"] == "Other"
    assert d["expected_present"] is True


def t_obs_collection_data_failure_is_not_ok():
    d = rc.obs_collection_data(get=lambda: (None, "OBS not reachable"))
    assert d == {"ok": False, "note": "OBS not reachable"}


def t_brands_op_argv():
    assert ui_ops.OPS["brands"] == ["brands"]
    assert ui_ops.build_argv("brands", {}) == ["brands"]


def t_discord_voice_ops():
    assert ui_ops.OPS["discord-voice-join"] == ["discord", "join"]
    assert ui_ops.OPS["discord-voice-leave"] == ["discord", "leave"]


def t_cookies_twitch_op():
    assert "cookies-twitch" in ui_ops.OPS
    argv = ui_ops.build_argv("cookies-twitch", {"browser": "firefox"})
    assert argv == ["cookies", "twitch", "firefox"]


def t_health_ops_in_registry():
    assert "health-export" in ui_ops.OPS
    assert "health-import" in ui_ops.OPS
    assert ui_ops.build_argv("health-export") == ["health", "export"]


def t_health_import_requires_file():
    # missing file -> ValueError
    try:
        ui_ops.build_argv("health-import", {})
        raise AssertionError("expected ValueError for missing file")
    except ValueError:
        pass
    # empty string also rejected (treated as absent)
    try:
        ui_ops.build_argv("health-import", {"file": ""})
        raise AssertionError("expected ValueError for empty file")
    except ValueError:
        pass
    # valid path -> appended as final positional arg
    argv = ui_ops.build_argv("health-import", {"file": "/tmp/x.jsonl"})
    assert argv == ["health", "import", "/tmp/x.jsonl"]
    assert argv[-1] == "/tmp/x.jsonl"


def t_health_import_rejects_control_chars():
    try:
        ui_ops.build_argv("health-import", {"file": "/tmp/x\x00y"})
        raise AssertionError("expected ValueError for control char in path")
    except ValueError:
        pass


def t_health_import_rejects_leading_dash():
    # A path starting with '-' must never reach the child process as an option flag.
    for bad in ("-rf", "--out", "-"):
        try:
            ui_ops.build_argv("health-import", {"file": bad})
            raise AssertionError(f"expected ValueError for leading-dash path: {bad!r}")
        except ValueError:
            pass


if __name__ == "__main__":
    import inspect, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in sorted(globals().items()):
            if name.startswith("t_") and callable(fn):
                # parameterized tests get exactly one positional arg: the tempdir
                fn(tmp) if inspect.signature(fn).parameters else fn()
                print("ok", name)
    print("ALL PASS")

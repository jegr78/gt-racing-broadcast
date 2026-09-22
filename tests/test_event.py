#!/usr/bin/env python3
"""Stdlib unit checks for the event-day readiness logic (src/scripts/event.py).
Run: python3 tests/test_event.py"""
import importlib.util, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "src", "scripts")
# event.py imports its siblings (preflight, install_apps) as plain modules.
sys.path.insert(0, SCRIPTS)
spec = importlib.util.spec_from_file_location("event", os.path.join(SCRIPTS, "event.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_probe_command_per_platform():
    assert m.probe_command("OBS", "darwin") == ["pgrep", "-x", "OBS"]
    assert m.probe_command("obs", "linux") == ["pgrep", "-x", "obs"]
    assert m.probe_command("obs64.exe", "win32") == \
        ["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"]


def t_parse_probe_posix_uses_returncode():
    assert m.parse_probe("darwin", 0, "", "OBS") is True
    assert m.parse_probe("linux", 1, "", "obs") is False


def t_parse_probe_windows_matches_stdout():
    # tasklist exits 0 even when nothing matches, so only the output counts.
    assert m.parse_probe("win32", 0, "obs64.exe   1234 Console", "obs64.exe") is True
    assert m.parse_probe("win32", 0, "INFO: No tasks are running...", "obs64.exe") is False
    assert m.parse_probe("win32", 0, "OBS64.EXE 1", "obs64.exe") is True  # case-insensitive
    assert m.parse_probe("win32", 0, None, "obs64.exe") is False


def t_process_names_cover_obs_and_discord():
    for app in ("obs", "discord"):
        for plat in ("darwin", "win32", "linux"):
            assert m._names(app, plat), (app, plat)


def t_app_running_returns_bool():
    # Smoke on the current platform: must not raise, must return a bool.
    assert m.app_running("obs") in (True, False)


def t_launch_command_darwin():
    assert m.launch_command("obs", "darwin") == (["open", "-a", "OBS"], None)
    assert m.launch_command("discord", "darwin") == (["open", "-a", "Discord"], None)
    assert m.launch_command("tailscale", "darwin") == (["open", "-a", "Tailscale"], None)


def t_launch_command_windows_obs_sets_cwd():
    env = {"ProgramFiles": r"C:\PF", "ProgramFiles(x86)": r"C:\PF86",
           "LOCALAPPDATA": r"C:\LAD"}
    obs = r"C:\PF\obs-studio\bin\64bit\obs64.exe"
    argv, cwd = m.launch_command("obs", "win32", env, exists=lambda p: p == obs)
    assert argv == [obs]
    assert cwd == r"C:\PF\obs-studio\bin\64bit"   # obs64 needs cwd at its bin dir


def t_launch_command_windows_discord_squirrel():
    env = {"ProgramFiles": "", "ProgramFiles(x86)": "", "LOCALAPPDATA": r"C:\LAD"}
    upd = r"C:\LAD\Discord\Update.exe"
    argv, cwd = m.launch_command("discord", "win32", env, exists=lambda p: p == upd)
    assert argv == [upd, "--processStart", "Discord.exe"]
    assert cwd is None


def t_launch_command_windows_tailscale_gui():
    gui = r"C:\Program Files\Tailscale\tailscale-ipn.exe"
    argv, cwd = m.launch_command("tailscale", "win32", {}, exists=lambda p: p == gui)
    assert argv == [gui] and cwd is None


def t_launch_command_windows_missing_is_none():
    assert m.launch_command("obs", "win32", {}, exists=lambda p: False) is None


def t_launch_command_linux():
    assert m.launch_command("obs", "linux", which=lambda n: "/usr/bin/obs") == \
        (["/usr/bin/obs"], None)
    assert m.launch_command("discord", "linux", which=lambda n: None) is None
    # tailscale on Linux is a daemon, so there is nothing to exec; hint instead.
    assert m.launch_command("tailscale", "linux") is None
    assert m.launch_command("companion", "linux") is None   # not a PATH-launched app


def _load_relay(name):
    path = os.path.join(ROOT, "src", "relay", name)
    s = importlib.util.spec_from_file_location(name.replace("-", "_")[:-3], path)
    mod = importlib.util.module_from_spec(s); s.loader.exec_module(mod)
    return mod


def t_check_assets():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "Overlay.png"), "w").close()
        assert m.check_assets(["Overlay.png"], d) == []
        assert m.check_assets(["Overlay.png", "Standby.png"], d) == ["Standby.png"]
        assert m.check_assets(["x.png"], os.path.join(d, "absent")) == ["x.png"]
        assert m.local_count(d) == 1
        assert m.local_count(os.path.join(d, "absent")) == 0


def t_required_graphics_from_assets_rows():
    gg = _load_relay("get-graphics.py")
    rows = [["Overlay", "https://drive.google.com/file/d/AAA1/view"],
            ["Intro Video", "https://youtu.be/xyz"],            # non-Drive: skipped
            ["Standby", "https://drive.google.com/file/d/BBB2/view"]]
    assert m.required_graphics(gg, rows) == ["Overlay.png", "Standby.png"]
    assert m.required_graphics(gg, None) == []
    # unsafe labels (path separators) are dropped, never become filenames
    rows_bad = [["../evil", "https://drive.google.com/file/d/CCC3/view"]]
    assert m.required_graphics(gg, rows_bad) == []


def t_required_media_from_assets_rows():
    gm = _load_relay("get-media.py")
    rows = [["Intro Video", "https://youtu.be/xyz"]]
    assert m.required_media(gm, rows) == ["intro.mp4"]
    # No media rows in the sheet -> require all three (the OBS scenes reference them).
    assert m.required_media(gm, [["Overlay", "u"]]) == \
        ["intro.mp4", "outro.mp4", "trailer.mp4"]
    assert m.required_media(gm, None) == ["intro.mp4", "outro.mp4", "trailer.mp4"]
    # A Sheet Trailer row flows through media_urls_from_csv -> included.
    rows3 = [["Trailer Video", "https://youtu.be/ttt"]]
    assert m.required_media(gm, rows3) == ["trailer.mp4"]


def t_fetch_assets_rows_handles_failure():
    gg = _load_relay("get-graphics.py")
    assert m.fetch_assets_rows(gg, None) is None          # no sheet id
    boom = type("GG", (), {"fetch_assets_csv":
                           staticmethod(lambda *a, **k: (_ for _ in ()).throw(OSError("net")))})
    assert m.fetch_assets_rows(boom, "SHEET") is None     # fetch failure -> None
    ok = type("GG", (), {"fetch_assets_csv": staticmethod(lambda *a, **k: "A,B\nC,D\n")})
    assert m.fetch_assets_rows(ok, "SHEET") == [["A", "B"], ["C", "D"]]


def t_classify_app_levels():
    assert m.classify_app("obs", True).level == "PASS"
    r = m.classify_app("obs", False)
    assert r.level == "FAIL" and r.name == "OBS"
    r = m.classify_app("discord", False)
    assert r.level == "WARN" and "interview audio" in r.detail
    # Web-variant host: no native Discord process; report an informational note.
    rw = m.classify_app("discord", False, web=True)
    assert rw.level == "INFO" and rw.name == "Discord"
    assert "Discord-web" in rw.detail and "browser" in rw.detail


def t_classify_tailscale():
    assert m.classify_tailscale("100.64.1.2").level == "PASS"
    assert "100.64.1.2" in m.classify_tailscale("100.64.1.2").detail
    miss = m.classify_tailscale(None)
    assert miss.level == "WARN"
    assert "Tailscale not connected" in miss.detail   # no 'tailnet' jargon


def t_classify_relay():
    assert m.classify_relay(True, True).level == "PASS"
    r = m.classify_relay(True, False)
    assert r.level == "FAIL" and "8088" in r.detail   # alive but port dead
    r = m.classify_relay(False, False)
    assert r.level == "FAIL" and "racecast relay start" in r.detail


def t_classify_companion():
    assert m.classify_companion(True, True).level == "PASS"
    r = m.classify_companion(False, True)
    assert r.level == "WARN" and "racecast companion start" in r.detail
    r = m.classify_companion(False, False, "manual on linux")
    assert r.level == "WARN" and "manual on linux" in r.detail


def t_classify_assets():
    # sheet readable, complete
    assert m.classify_assets("Graphics", [], 9, "FAIL", "run `racecast graphics`").level == "PASS"
    # sheet readable, files missing -> severity, names listed
    r = m.classify_assets("Graphics", ["Standby.png"], 8, "FAIL", "run `racecast graphics`")
    assert r.level == "FAIL" and "Standby.png" in r.detail and "racecast graphics" in r.detail
    r = m.classify_assets("Media", ["outro.mp4"], 1, "WARN", "run `racecast media`")
    assert r.level == "WARN"
    # sheet unreachable (missing=None) -> local fallback
    r = m.classify_assets("Graphics", None, 9, "FAIL", "run `racecast graphics`")
    assert r.level == "WARN" and "not verified" in r.detail
    r = m.classify_assets("Graphics", None, 0, "FAIL", "run `racecast graphics`")
    assert r.level == "FAIL"      # nothing local at all


def t_classify_env():
    assert m.classify_env("sheet", "http://push").level == "PASS"
    r = m.classify_env("sheet", "")          # push URL optional -> WARN, not FAIL
    assert r.level == "WARN" and "RACECAST_SHEET_PUSH_URL" in r.detail
    r = m.classify_env("", "http://push")
    assert r.level == "FAIL" and "RACECAST_SHEET_ID" in r.detail


def t_go_live_reminder():
    assert m.GO_LIVE_REMINDER.level == "INFO"
    assert "refresh" in m.GO_LIVE_REMINDER.detail.lower()
    assert "HUD" in m.GO_LIVE_REMINDER.detail


def t_wait_until_up():
    elapsed = {"n": 0}
    clock = lambda: elapsed["n"]            # fake monotonic: sleeps advance it
    def sleep(secs):
        elapsed["n"] += secs
    # all up immediately -> returns without sleeping
    st = m.wait_until_up({"relay": lambda: True}, timeout=60, interval=5,
                         clock=clock, sleep=sleep)
    assert st == {"relay": True} and elapsed["n"] == 0
    # comes up on the third poll -> exactly two sleeps
    polls = {"k": 0}
    def flaky():
        polls["k"] += 1
        return polls["k"] >= 3
    st = m.wait_until_up({"obs": flaky}, timeout=60, interval=5,
                         clock=clock, sleep=sleep)
    assert st == {"obs": True} and elapsed["n"] == 10
    # never up -> stops at the deadline, reports the loser as False
    elapsed["n"] = 0
    st = m.wait_until_up({"relay": lambda: False, "obs": lambda: True},
                         timeout=60, interval=5, clock=clock, sleep=sleep)
    assert st == {"relay": False, "obs": True}
    assert elapsed["n"] == 60
    # a probe that turned True is cached, not re-polled
    polls["k"] = 0
    elapsed["n"] = 0
    m.wait_until_up({"obs": flaky, "relay": lambda: False},
                    timeout=10, interval=5, clock=clock, sleep=sleep)
    assert polls["k"] == 3                  # True after 3 polls, then cached


def t_director_urls():
    lines = m.director_urls("100.64.1.2", companion_port=8000)
    assert len(lines) == 4
    assert lines[0] == "Share with your directors:"
    assert "http://100.64.1.2:8088/panel" in lines[1]
    assert "http://100.64.1.2:8000/tablet" in lines[2]
    assert "OBS WebSocket password" in lines[3]
    # custom companion port flows through to the tablet URL
    assert "8123/tablet" in m.director_urls("100.64.1.2", companion_port=8123)[2]
    # custom relay port flows through to the panel URL
    assert "9001/panel" in m.director_urls("100.64.1.2", relay_port=9001)[1]


def t_director_urls_no_tailscale():
    # No tailnet IP -> a notice instead of URLs (directors cannot connect)
    lines = m.director_urls(None)
    assert len(lines) == 2
    assert lines[0] == "Share with your directors:"
    assert "Tailscale not connected" in lines[1]
    assert "racecast tailscale up" in lines[1]


def t_quit_command_per_platform():
    assert m.quit_command("obs", "darwin") == \
        ["osascript", "-e", 'tell application "OBS" to quit']
    assert m.quit_command("discord", "darwin") == \
        ["osascript", "-e", 'tell application "Discord" to quit']
    assert m.quit_command("obs", "win32") == ["taskkill", "/IM", "obs64.exe"]
    assert m.quit_command("discord", "win32") == ["taskkill", "/IM", "Discord.exe"]
    assert m.quit_command("obs", "linux") == ["pkill", "-f", "obs"]


def t_quit_command_tailscale_gui():
    # The Tailscale GUI app quits on macOS/Windows; on Linux it's a daemon (None).
    assert m.quit_command("tailscale", "darwin") == \
        ["osascript", "-e", 'tell application "Tailscale" to quit']
    assert m.quit_command("tailscale", "win32") == \
        ["taskkill", "/IM", "tailscale-ipn.exe"]
    assert m.quit_command("tailscale", "linux") is None


def t_quit_command_unknown_app():
    assert m.quit_command("relay", "linux") is None
    assert m.quit_command("companion", "darwin") is None      # own stop path


# The OBS scene-collection readiness line.
def t_classify_scene_collection_skipped_when_status_none():
    r = m.classify_scene_collection(None, "OBS WebSocket not reachable")
    assert r.level == m.WARN
    assert "skipped" in r.detail
    assert "not reachable" in r.detail


def t_classify_scene_collection_match_is_pass():
    status = {"current": "GT Racing Endurance", "expected": "GT Racing Endurance",
              "available": ["GT Racing Endurance"], "match": True,
              "expected_present": True, "renamed_variant": None}
    r = m.classify_scene_collection(status, "")
    assert r.level == m.PASS
    assert "GT Racing Endurance" in r.detail


def t_classify_scene_collection_wrong_but_present_warns_with_fix():
    status = {"current": "Other", "expected": "GT Racing Endurance",
              "available": ["GT Racing Endurance", "Other"], "match": False,
              "expected_present": True, "renamed_variant": None}
    r = m.classify_scene_collection(status, "")
    assert r.level == m.WARN
    assert "racecast obs collection set" in r.detail


def t_classify_scene_collection_renamed_variant_warns_manual():
    status = {"current": "GT Racing Endurance 2", "expected": "GT Racing Endurance",
              "available": ["GT Racing Endurance 2"], "match": False,
              "expected_present": False, "renamed_variant": "GT Racing Endurance 2"}
    r = m.classify_scene_collection(status, "")
    assert r.level == m.WARN
    assert "renamed" in r.detail


def t_classify_scene_collection_absent_warns_import():
    status = {"current": "Scene", "expected": "GT Racing Endurance",
              "available": ["Scene"], "match": False,
              "expected_present": False, "renamed_variant": None}
    r = m.classify_scene_collection(status, "")
    assert r.level == m.WARN
    assert "not found" in r.detail


def t_classify_scene_collection_overlap_prefers_switch_over_manual():
    # expected_present must win over renamed_variant: the real collection is
    # present, so the advice is `racecast obs collection set`, matching the CLI
    # and the Control Center.
    status = {"current": "GT Racing Endurance 2", "expected": "GT Racing Endurance",
              "available": ["GT Racing Endurance", "GT Racing Endurance 2"], "match": False,
              "expected_present": True, "renamed_variant": "GT Racing Endurance 2"}
    r = m.classify_scene_collection(status, "")
    assert r.level == m.WARN
    assert "racecast obs collection set" in r.detail
    assert "manually" not in r.detail


def t_gate_blockers_keeps_only_fails():
    # The pre-flight gate blocks event-start bring-up only on FAIL-level static
    # preconditions; PASS/WARN/INFO are advisory and must never block.
    results = [
        m.Result(m.PASS, ".env", "ok"),
        m.Result(m.WARN, "Media", "missing intro.mp4"),
        m.Result(m.FAIL, ".env", "missing RACECAST_SHEET_ID"),
        m.Result(m.FAIL, "Graphics", "missing Standby.png"),
        m.Result(m.INFO, "note", "fyi"),
    ]
    blockers = m.gate_blockers(results)
    assert [r.name for r in blockers] == [".env", "Graphics"]
    # nothing FAIL -> empty list (gate is open)
    assert m.gate_blockers([m.Result(m.PASS, "x", ""), m.Result(m.WARN, "y", "")]) == []
    assert m.gate_blockers([]) == []


def t_launch_env_linux_ssh_sets_display():
    env = {"HOME": "/home/op"}
    out = m.launch_env("obs", "linux", env, exists=lambda p: p == "/home/op/.Xauthority")
    assert out == {"DISPLAY": ":0", "XAUTHORITY": "/home/op/.Xauthority"}


def t_launch_env_display_without_xauthority():
    out = m.launch_env("discord", "linux", {"HOME": "/h"}, exists=lambda p: False)
    assert out == {"DISPLAY": ":0"}


def t_launch_env_respects_existing_display():
    assert m.launch_env("obs", "linux", {"DISPLAY": ":1", "HOME": "/h"}) == {}


def t_launch_env_racecast_display_override():
    out = m.launch_env("discord", "linux",
                       {"RACECAST_DISPLAY": ":7", "HOME": "/h"}, exists=lambda p: False)
    assert out == {"DISPLAY": ":7"}


def t_launch_env_noop_non_linux():
    assert m.launch_env("obs", "darwin", {}) == {}
    assert m.launch_env("obs", "win32", {}) == {}


def t_launch_env_noop_non_gui():
    assert m.launch_env("tailscale", "linux", {"HOME": "/h"}) == {}


# The session runtime dir, where PipeWire audio and the Discord IPC socket live.
# A GUI app started over SSH without XDG_RUNTIME_DIR cannot reach PipeWire (its socket is
# $XDG_RUNTIME_DIR/pipewire-0), so OBS' Application Audio Capture silently fails to create
# and racecast's Discord voice auto-join finds no IPC endpoint. Both fail without an error
# on any visible surface, so launch_env must supply the variable.

def t_launch_env_fills_runtime_dir_and_dbus():
    out = m.launch_env("obs", "linux", {"HOME": "/h"},
                       exists=lambda p: p in ("/run/user/1000", "/run/user/1000/bus"),
                       uid=1000, glob_paths=lambda pat: [])
    assert out["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert out["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"


def t_launch_env_runtime_dir_without_dbus_socket():
    # The runtime dir exists but carries no session bus -> set only what is real.
    out = m.launch_env("obs", "linux", {"HOME": "/h"},
                       exists=lambda p: p == "/run/user/1000",
                       uid=1000, glob_paths=lambda pat: [])
    assert out["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert "DBUS_SESSION_BUS_ADDRESS" not in out


def t_launch_env_keeps_inherited_runtime_dir():
    # Already in the environment -> never overridden, and it is the search base.
    out = m.launch_env("obs", "linux", {"HOME": "/h", "XDG_RUNTIME_DIR": "/run/user/42/"},
                       exists=lambda p: p == "/run/user/42/bus",
                       uid=1000, glob_paths=lambda pat: [])
    assert "XDG_RUNTIME_DIR" not in out
    assert out["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/42/bus"


def t_launch_env_xauthority_from_runtime_dir():
    # SDDM/GDM keep the X cookie in the runtime dir, NOT in ~/.Xauthority. Without
    # this fall-back OBS does not start at all over SSH on a KDE/GNOME box.
    seen = []

    def globber(pattern):
        seen.append(pattern)
        return ["/run/user/1000/xauth_ZZZZZZ", "/run/user/1000/xauth_AAAAAA"]

    out = m.launch_env("obs", "linux", {"HOME": "/h"},
                       exists=lambda p: p == "/run/user/1000",
                       uid=1000, glob_paths=globber)
    assert seen == ["/run/user/1000/xauth_*"]
    # deterministic pick (sorted), so two runs never disagree
    assert out["XAUTHORITY"] == "/run/user/1000/xauth_AAAAAA"


def t_launch_env_home_xauthority_wins_over_runtime_dir():
    out = m.launch_env("obs", "linux", {"HOME": "/h"},
                       exists=lambda p: p in ("/h/.Xauthority", "/run/user/1000"),
                       uid=1000, glob_paths=lambda pat: ["/run/user/1000/xauth_A"])
    assert out["XAUTHORITY"] == "/h/.Xauthority"


def t_launch_env_no_runtime_dir_at_all():
    # Nothing discoverable -> exactly the pre-existing behaviour, no invented paths.
    out = m.launch_env("obs", "linux", {"HOME": "/h"}, exists=lambda p: False,
                       uid=1000, glob_paths=lambda pat: [])
    assert out == {"DISPLAY": ":0"}


def t_launch_env_builds_linux_paths_with_forward_slashes():
    # These are fixed-OS Linux paths, so they must never be assembled with
    # os.path.join (backslashes on the Windows CI runner).
    out = m.launch_env("obs", "linux", {"HOME": "/h"},
                       exists=lambda p: p in ("/run/user/1000", "/run/user/1000/bus"),
                       uid=1000, glob_paths=lambda pat: [])
    assert "\\" not in "".join(out.values())


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

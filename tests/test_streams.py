#!/usr/bin/env python3
"""Stdlib checks for the static-streams helpers. Run: python3 tests/test_streams.py"""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# loopstream.py imports its sibling `services` for external_tool_env, which is
# always on sys.path in production, so mirror that for the loader.
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


start = _load("start_streams", os.path.join("src", "scripts", "start-streams.py"))
stop = _load("stop_streams", os.path.join("src", "scripts", "stop-streams.py"))
loop = _load("loopstream", os.path.join("src", "scripts", "loopstream.py"))
feeds_x = _load("feeds_x", os.path.join("src", "relay", "racecast-feeds.py"))


def t_feed_argv_repo_includes_log():
    argv = start.feed_argv(False, "/py", "/loop.py", "UC123", "53001", "/r/logs/feed_53001.log")
    assert argv == ["/py", "/loop.py", "UC123", "53001", "--log", "/r/logs/feed_53001.log"]


def t_feed_argv_frozen_includes_log():
    argv = start.feed_argv(True, "/bin/racecast", "/loop.py", "UC123", "53001", "/r/logs/feed_53001.log")
    assert argv == ["/bin/racecast", "streams", "run-feed", "UC123", "53001", "--log", "/r/logs/feed_53001.log"]


def t_state_dirs_match():
    repo = os.path.join(ROOT, "src", "scripts")
    assert start.state_dir(repo) == stop.state_dir(repo)
    dist = os.path.join("pkg", "scripts")  # distributed-package layout
    assert start.state_dir(dist) == stop.state_dir(dist) == dist


def t_feed_env_repo_inherits():
    assert start.feed_env(False, {"A": "1"}) is None  # None -> Popen inherits


def t_feed_env_frozen_resets_pyinstaller():
    env = start.feed_env(True, {"A": "1"})
    assert env["A"] == "1"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def t_feed_process_matchers():
    # POSIX `ps -o command=` lines
    assert stop.looks_like_feed("python3 /x/loopstream.py UC1 53001")
    assert stop.looks_like_feed("/usr/local/bin/streamlink --player-external-http ...")
    assert stop.looks_like_feed("/apps/racecast streams run-feed UC1 53001")  # frozen child
    assert not stop.looks_like_feed("/usr/bin/vim notes.txt")
    assert not stop.looks_like_feed("python3 some_other_tool.py")  # bare python on POSIX is NOT a feed
    # Windows `tasklist` CSV-ish output
    assert stop.looks_like_feed('"python.exe","123",...', windows=True)
    assert stop.looks_like_feed('"streamlink.exe","123",...', windows=True)
    assert stop.looks_like_feed('"racecast.exe","123",...', windows=True)   # frozen child
    assert not stop.looks_like_feed('"notepad.exe","123",...', windows=True)


def t_kill_tree_reaps_grandchild_session():
    """A static feed is spawned as a session leader (start_new_session=True). In
    the frozen binary the tree is bootloader(leader) -> app-child -> streamlink,
    so streamlink is a grandchild. kill_tree must reap the whole process group,
    or the grandchild orphans and keeps its port. POSIX-only: Windows
    `taskkill /T` already kills the tree. (#133)"""
    import sys, time, tempfile, subprocess, signal, shutil
    if os.name == "nt":
        return

    def alive(pid):
        try:
            os.kill(pid, 0); return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    d = tempfile.mkdtemp()
    leader = None
    gc_pid = None
    try:
        tree = os.path.join(d, "tree.py")
        stamp = os.path.join(d, "gc.pid")
        # Recursive tree: depth>0 re-spawns itself one level deeper, and depth 0
        # is the grandchild, which records its PID and sleeps. Depth 2 gives
        # leader, child, grandchild.
        with open(tree, "w") as f:
            f.write("import os, sys, subprocess, time\n"
                    "depth = int(sys.argv[1]); stamp = sys.argv[2]\n"
                    "if depth > 0:\n"
                    "    subprocess.run([sys.executable, sys.argv[0], str(depth - 1), stamp])\n"
                    "else:\n"
                    "    open(stamp, 'w').write(str(os.getpid()))\n"
                    "    time.sleep(30)\n")
        # start_new_session mirrors services.spawn_kwargs('posix'), so the leader
        # is its own session and group leader, like a spawned static feed.
        leader = subprocess.Popen([sys.executable, tree, "2", stamp], start_new_session=True)
        for _ in range(200):                       # up to ~10 s for the grandchild to start
            try:
                with open(stamp) as fh:
                    txt = fh.read().strip()
                if txt:
                    gc_pid = int(txt); break
            except (OSError, ValueError):
                pass  # not written yet or partial; the next tick reads it
            time.sleep(0.05)
        assert gc_pid is not None, "grandchild never started"
        ppid = int(subprocess.check_output(["ps", "-o", "ppid=", "-p", str(gc_pid)]).strip())
        assert ppid != leader.pid, "setup error: grandchild is a direct child of the leader"
        assert alive(gc_pid)

        stop.kill_tree(leader.pid)

        for _ in range(200):                       # up to ~10 s for the group to die
            if not alive(gc_pid):
                break
            time.sleep(0.05)
        assert not alive(gc_pid), \
            "grandchild survived kill_tree — only direct children were reaped (#133)"
    finally:
        for pid in (gc_pid, leader.pid if leader else None):
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass  # best-effort teardown; the process is already gone
        if leader:
            try:
                leader.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass  # best-effort reap of the test's own child
        shutil.rmtree(d, ignore_errors=True)


def t_load_feeds_falls_back_to_builtin():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert start.load_feeds(d) == start.FEEDS        # no streams.json -> built-in


def t_load_feeds_reads_config():
    import json, tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "streams.json"), "w") as fh:
            json.dump([{"label": "X", "channel": "UCNye-wNBqNL5ZzHSJj3l8Bg", "port": "53005"},
                       {"channel": "UCknLrEdhRCp1aegoMqRaCZg", "port": "53006"}], fh)
        assert start.load_feeds(d) == [("UCNye-wNBqNL5ZzHSJj3l8Bg", "53005"),
                                       ("UCknLrEdhRCp1aegoMqRaCZg", "53006")]


def t_load_feeds_skips_incomplete_and_bad_json():
    import json, tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "streams.json")
        with open(p, "w") as fh:
            json.dump([{"channel": "UCNye-wNBqNL5ZzHSJj3l8Bg", "port": ""},  # no port -> skip
                       {"channel": "", "port": "53006"},                      # no channel -> skip
                       {"channel": "UCknLrEdhRCp1aegoMqRaCZg", "port": "53007"}], fh)
        assert start.load_feeds(d) == [("UCknLrEdhRCp1aegoMqRaCZg", "53007")]
        with open(p, "w") as fh:
            fh.write("not json")                            # malformed -> FEEDS
        assert start.load_feeds(d) == start.FEEDS


def t_loop_no_window_kwargs_per_os():
    # A static feed runs detached with no console, so on Windows its streamlink
    # child would otherwise pop a persistent terminal window. CREATE_NO_WINDOW
    # applies there only.
    assert loop.no_window_kwargs("nt") == {"creationflags": 0x08000000}
    assert loop.no_window_kwargs("posix") == {}
    assert loop.no_window_kwargs("java") == {}


def t_loop_streamlink_argv_serves_the_port():
    argv = loop.streamlink_argv("https://www.youtube.com/channel/UC9/live", "53005")
    assert argv[0] == "streamlink"
    assert "https://www.youtube.com/channel/UC9/live" in argv
    assert "--player-external-http-port" in argv and "53005" in argv


def t_loop_serve_once_passes_no_window():
    # The feed's stdout is redirected to a log by start_streams, so the flag
    # suppresses the window without losing logged output.
    captured = {}

    def fake_call(argv, **kw):
        captured["argv"], captured["kw"] = argv, kw
        return 0

    assert loop.serve_once("https://x/live", "53005", call=fake_call) == 0
    assert captured["argv"][0] == "streamlink"
    for k, v in loop.no_window_kwargs().items():
        assert captured["kw"].get(k) == v
    # streamlink is spawned with a sanitized env so a frozen binary's bundled
    # libs do not leak into the system-linked tool.
    assert "env" in captured["kw"]
    assert captured["kw"]["env"] == loop.external_tool_env()


def t_loop_youtube_argv_unchanged():
    argv = loop.streamlink_argv("https://www.youtube.com/channel/UC123/live", "53001")
    assert "--twitch-low-latency" not in argv
    assert "1080p60,1080p,720p60,720p" in argv          # static's YouTube quality preserved
    assert "--hls-live-edge" in argv and argv[argv.index("--hls-live-edge")+1] == "4"


def t_loop_twitch_argv():
    argv = loop.streamlink_argv("https://www.twitch.tv/chan", "53002", platform="twitch")
    assert "--twitch-low-latency" in argv
    assert argv[argv.index("--hls-live-edge")+1] == "2"
    assert "--twitch-disable-ads" not in argv
    assert argv[-2:] == ["https://www.twitch.tv/chan", "best"]
    assert "--" in argv and argv.index("--") < argv.index("https://www.twitch.tv/chan")


def t_loop_twitch_argv_token():
    argv = loop.streamlink_argv("https://www.twitch.tv/chan", "53002",
                                platform="twitch", twitch_token="abc123")
    i = argv.index("--twitch-api-header")
    assert argv[i+1] == "Authorization=OAuth abc123"
    assert i < argv.index("--")


def t_loop_channel_url_and_platform():
    assert loop.channel_url("UC123").startswith("https://www.youtube.com/channel/UC123/live")
    assert loop.channel_url("https://www.twitch.tv/x") == "https://www.twitch.tv/x"
    assert loop.platform_of("https://www.twitch.tv/x") == "twitch"
    assert loop.platform_of("UCabc") == "youtube"   # bare id -> no scheme -> host empty -> youtube


def t_loop_crosscheck_relay():
    # The duplicated Twitch bits must stay equal to the relay's.
    assert loop.STREAMLINK_TWITCH == feeds_x.STREAMLINK_TWITCH
    for u in ["https://www.youtube.com/watch?v=a", "https://youtu.be/a",
              "https://www.twitch.tv/c", "https://m.twitch.tv/c", "https://twitch.tv@evil.com/"]:
        assert loop.platform_of(u) == feeds_x.platform_of(u)
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "twitch-cookies.txt")
    with open(p, "w") as f:
        f.write(".twitch.tv\tTRUE\t/\tTRUE\t0\tauth-token\tdeadbeef\n")
    assert loop.twitch_oauth_from_cookies(p) == feeds_x.twitch_oauth_from_cookies(p) == "deadbeef"
    import inspect
    for fn in ("platform_of", "twitch_oauth_from_cookies", "channel_url"):
        assert inspect.getsource(getattr(loop, fn)) == inspect.getsource(getattr(feeds_x, fn)), \
            f"{fn} drifted between loopstream and racecast-feeds"


def t_start_is_channel_accepts_ids_and_allowed_urls():
    assert start.is_channel("UCNye-wNBqNL5ZzHSJj3l8Bg") is True
    assert start.is_channel("https://www.youtube.com/watch?v=abc") is True
    assert start.is_channel("https://www.twitch.tv/chan") is True
    assert start.is_channel("https://evil.com/x") is False
    assert start.is_channel("https://twitch.tv@evil.com/") is False   # userinfo trick
    assert start.is_channel("file:///etc/passwd") is False


def t_start_load_feeds_skips_invalid(tmp=None):
    import tempfile, json, os as _os
    d = tempfile.mkdtemp()
    with open(_os.path.join(d, "streams.json"), "w") as f:
        json.dump([{"channel": "UCNye-wNBqNL5ZzHSJj3l8Bg", "port": "53001"},
                   {"channel": "https://www.twitch.tv/chan", "port": "53002"},
                   {"channel": "https://evil.com/x", "port": "53003"}], f)
    feeds = start.load_feeds(d)
    chans = [c for c, p in feeds]
    assert "https://www.twitch.tv/chan" in chans
    assert "UCNye-wNBqNL5ZzHSJj3l8Bg" in chans
    assert "https://evil.com/x" not in chans          # SSRF host not allowed -> skipped


def t_start_crosscheck_is_channel_vs_relay():
    import inspect
    for fn in ("is_channel", "_is_stream_url"):
        assert inspect.getsource(getattr(start, fn)) == inspect.getsource(getattr(feeds_x, fn)), \
            f"{fn} drifted between start-streams and racecast-feeds"
    assert start.CHANNEL_RE.pattern == feeds_x.CHANNEL_RE.pattern


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

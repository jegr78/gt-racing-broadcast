#!/usr/bin/env python3
"""Relay live-video tracker + channel-ID resolver (audience follows the live
broadcast). Fake runners only, no real yt-dlp.
Run: python3 tests/test_audience_follow_live.py"""
import importlib.util, os, subprocess, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("RACECAST_LOG_DIR", tempfile.mkdtemp(prefix="racecast-test-logs-"))
spec = importlib.util.spec_from_file_location(
    "irofeeds_afl", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

CH = "https://www.youtube.com/@somechannel"


def _patch(**kw):
    old = {k: getattr(m, k) for k in kw}
    for k, v in kw.items():
        setattr(m, k, v)
    return old


def _restore(old):
    for k, v in old.items():
        setattr(m, k, v)


def t_live_lookup_tells_failure_from_not_live():
    real_run = m.subprocess.run

    def run_code1(cmd, **kw):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    def run_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    old = _patch(_yt_run_json=lambda cmd, timeout=30: {
        "entries": [{"id": "vodAAAAAAAA", "live_status": "not_live"}]})
    try:
        m.subprocess.run = run_code1
        assert m.yt_live_video_lookup(CH, None) == []
        _patch(_yt_run_json=lambda cmd, timeout=30: None)
        # JSON unreadable and /live gives nothing -> failure
        assert m.yt_live_video_lookup(CH, None) is None
        m.subprocess.run = run_timeout
        assert m.yt_live_video_lookup(CH, None) is None
        assert m.yt_live_video_ids(CH, None) == []
        _patch(_yt_run_json=lambda cmd, timeout=30: {"entries": [
            {"id": "liveAAAAAAA", "live_status": "is_live"},
            {"id": "liveBBBBBBB", "is_live": True},
            {"id": "vodCCCCCCCC", "live_status": "was_live"}]})
        assert m.yt_live_video_lookup(CH, None) == ["liveAAAAAAA", "liveBBBBBBB"]
    finally:
        m.subprocess.run = real_run
        _restore(old)


def t_channel_id_reads_yt_dlp_metadata():
    good = "UC" + "a" * 22
    seen = {}

    def fake(cmd, timeout=30):
        seen["cmd"] = cmd
        return {"channel_id": good}
    old = _patch(_yt_run_json=fake)
    try:
        assert m.yt_channel_id(CH, None) == good
        assert "--flat-playlist" in seen["cmd"] and "-J" in seen["cmd"]
        _patch(_yt_run_json=lambda cmd, timeout=30: {"channel_id": "nope"})
        assert m.yt_channel_id(CH, None) is None
        _patch(_yt_run_json=lambda cmd, timeout=30: None)
        assert m.yt_channel_id(CH, None) is None
    finally:
        _restore(old)


def t_tracker_reuses_a_fresh_result_and_retries_a_failure():
    now = [100.0]
    calls = []
    results = [["vidAAAAAAAA"], ["vidBBBBBBBB"]]

    def lookup(channel, cookies, **kw):
        calls.append(channel)
        return results[len(calls) - 1] if len(calls) <= len(results) else None
    tr = m.LiveVideoTracker(None, lookup=lookup, max_age=25, clock=lambda: now[0])
    assert tr.lookup(CH) == ["vidAAAAAAAA"]
    now[0] += 10
    assert tr.lookup(CH) == ["vidAAAAAAAA"]
    assert len(calls) == 1
    now[0] += 16   # +26 s total
    assert tr.lookup(CH) == ["vidBBBBBBBB"]
    assert len(calls) == 2
    now[0] += 26
    assert tr.lookup(CH) is None          # failure: not served from the stale cache
    assert len(calls) == 3
    now[0] += 1
    assert tr.lookup(CH) is None          # not cached as a success, retried
    assert len(calls) == 4


def t_supervisor_uses_the_shared_tracker():
    def boom(*a, **k):
        raise AssertionError("yt_live_video_ids must not be called")
    old = _patch(yt_live_video_ids=boom)
    try:
        tracker = types.SimpleNamespace(lookup=lambda ch: ["vidAAAAAAAA", "vidBBBBBBBB"])
        src = types.SimpleNamespace(get=lambda: [("youtube", CH)])
        sup = m.BroadcastChatSupervisor(m.BroadcastChatStore(), src, None, tracker=tracker)
        assert sorted(sup._desired()) == ["vidAAAAAAAA", "vidBBBBBBBB"]
        failing = types.SimpleNamespace(lookup=lambda ch: None)
        sup2 = m.BroadcastChatSupervisor(m.BroadcastChatStore(), src, None, tracker=failing)
        assert sup2._desired() == {}
    finally:
        _restore(old)


def t_follow_hooks_filter_youtube_rows_and_refresh_rarely():
    class Src:
        def __init__(self):
            self.refreshes = 0
        def refresh(self):
            self.refreshes += 1
        def get(self):
            return [("youtube", "@a"), ("twitch", "x"), ("youtube", "UC" + "b" * 22)]
    now = [100.0]
    src = Src()
    tracker = types.SimpleNamespace(lookup=lambda ch: ["vidAAAAAAAA"])
    spawned = []
    hooks = m.audience_follow_hooks(src, tracker, None, clock=lambda: now[0],
                                    spawn=lambda fn: (spawned.append(fn), fn()))
    want = ["@a", "UC" + "b" * 22]
    assert hooks["channels"]() == want
    assert hooks["channels"]() == want
    assert src.refreshes == 1
    now[0] += 31
    assert hooks["channels"]() == want
    assert src.refreshes == 2
    assert hooks["live_lookup"] is tracker.lookup
    old = _patch(yt_channel_id=lambda text, cookies, timeout=25: "ID:" + text)
    try:
        assert hooks["channel_id_lookup"]("@a") == "ID:@a"
    finally:
        _restore(old)


def t_follow_channels_never_waits_for_a_running_refresh():
    started = []
    class Src:
        def refresh(self):
            raise AssertionError("must not run inline")
        def get(self):
            return [("youtube", "@a")]
    now = [0.0]
    hooks = m.audience_follow_hooks(Src(), None, None, clock=lambda: now[0],
                                    spawn=started.append)
    assert hooks["channels"]() == ["@a"]     # cached rows, refresh only queued
    assert len(started) == 1
    now[0] += 100
    assert hooks["channels"]() == ["@a"]     # still running -> no second one
    assert len(started) == 1
    class Quiet(Src):
        def refresh(self):
            pass
    hooks2 = m.audience_follow_hooks(Quiet(), None, None, clock=lambda: now[0],
                                     spawn=started.append)
    hooks2["channels"](); started.pop()()    # run it to completion
    now[0] += 100
    hooks2["channels"]()
    assert len(started) == 2                 # finished -> next stale call may start


def t_follow_hooks_are_none_without_a_channel_tab():
    assert m.audience_follow_hooks(None, None, None) == {
        "channels": None, "channels_loaded": None, "live_lookup": None,
        "channel_id_lookup": None}


def t_follow_hooks_report_the_first_load():
    class Src:
        def __init__(self):
            self.loaded, self.rows, self.fail, self.refreshes = False, [], True, 0
        def refresh(self):
            self.refreshes += 1
            if self.fail:
                return False
            self.rows, self.loaded = [("youtube", "@a")], True
            return True
        def get(self):
            return list(self.rows)
    now = [0.0]
    src = Src()
    hooks = m.audience_follow_hooks(src, None, None, clock=lambda: now[0],
                                    spawn=lambda fn: fn())
    assert hooks["channels_loaded"]() is False
    assert hooks["channels"]() == [] and src.refreshes == 1
    now[0] += 5
    hooks["channels"]()
    assert src.refreshes == 2, "a failed first read must be retried soon"
    src.fail = False
    now[0] += 5
    assert hooks["channels"]() == ["@a"] and hooks["channels_loaded"]() is True
    now[0] += 5
    hooks["channels"]()
    assert src.refreshes == 3, "once loaded the normal cadence applies"
    plain = m.audience_follow_hooks(types.SimpleNamespace(refresh=lambda: True,
                                                          get=lambda: []),
                                    None, None, spawn=lambda fn: None)
    assert plain["channels_loaded"]() is True   # a source without the flag


def t_channel_source_marks_its_first_successful_read():
    src = m.ChannelSource("http://x/csv")
    assert src.loaded is False
    src._fetch_text = lambda timeout=15: None
    assert src.refresh() is False and src.loaded is False
    src._fetch_text = lambda timeout=15: "Platform,Channel\nYouTube,@a\n"
    assert src.refresh() is True and src.loaded is True


def t_follow_wiring_without_broadcast_chat():
    existing = object()
    assert m.live_channel_source(existing, "http://x", "rt") is existing
    made = m.live_channel_source(None, "http://x/csv", "rt")
    assert isinstance(made, m.ChannelSource) and made.csv_url == "http://x/csv"
    assert made.cache_path == os.path.join("rt", "channel.cache.txt")
    assert m.live_channel_source(None, None, "rt") is None


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            try:
                fn(); print("ok  ", name)
            except Exception as e:
                fails += 1; print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if fails else 0)

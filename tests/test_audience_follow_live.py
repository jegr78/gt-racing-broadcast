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


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            try:
                fn(); print("ok  ", name)
            except Exception as e:
                fails += 1; print("FAIL", name, type(e).__name__, e)
    sys.exit(1 if fails else 0)

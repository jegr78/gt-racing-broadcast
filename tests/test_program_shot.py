#!/usr/bin/env python3
"""Unit checks for the relay's program-monitor screenshot TTL cache. Without it each
console view polling /preview/program and /cockpit/program opened its own
obs-websocket connection for the same program image.
Run: python3 tests/test_program_shot.py"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class _Counter:
    """A fetcher that returns a fixed frame and counts how often OBS was hit."""
    def __init__(self, data=b"JPEG", note=""):
        self.calls = 0
        self.data = data
        self.note = note

    def __call__(self):
        self.calls += 1
        return self.data, self.note


def t_hit_within_ttl_skips_fetch():
    c = m.ProgramShotCache(ttl_s=1.0)
    f = _Counter()
    assert c.fetch(f, now=100.0) == (b"JPEG", "")
    # a second poll 0.9s later (< TTL) must NOT hit OBS again
    assert c.fetch(f, now=100.9) == (b"JPEG", "")
    assert f.calls == 1, f.calls


def t_refetches_after_ttl():
    c = m.ProgramShotCache(ttl_s=1.0)
    f = _Counter()
    c.fetch(f, now=100.0)
    c.fetch(f, now=101.0)          # exactly TTL later -> expired (strict <)
    c.fetch(f, now=101.05)         # cached again from the 101.0 fetch
    assert f.calls == 2, f.calls


def t_many_concurrent_views_share_one_fetch():
    # N views polling inside one TTL window collapse to a single OBS hit.
    c = m.ProgramShotCache(ttl_s=1.0)
    f = _Counter()
    for i in range(20):
        c.fetch(f, now=100.0 + i * 0.02)   # 20 polls spread over 0.4s (< TTL)
    assert f.calls == 1, f.calls


def t_failed_fetch_is_not_cached():
    c = m.ProgramShotCache(ttl_s=1.0)
    fail = _Counter(data=None, note="obs unreachable")
    assert c.fetch(fail, now=100.0) == (None, "obs unreachable")
    # a None result must not be served from cache; the next poll retries OBS
    ok = _Counter(data=b"JPEG")
    assert c.fetch(ok, now=100.1) == (b"JPEG", "")
    assert fail.calls == 1 and ok.calls == 1


def t_note_dropped_on_cache_hit():
    # a cache hit reports no note even if the original fetch carried one
    c = m.ProgramShotCache(ttl_s=5.0)
    c.fetch(_Counter(data=b"A", note="warn"), now=0.0)
    data, note = c.fetch(_Counter(data=b"B"), now=1.0)
    assert data == b"A" and note == ""      # served the cached frame, no note


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

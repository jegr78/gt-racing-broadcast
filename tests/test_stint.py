#!/usr/bin/env python3
"""Stdlib unit checks for the producer-handover stint positioning.
Run: python3 tests/test_stint.py"""
import importlib.util, json, os, tempfile, threading, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Relay constructs Feeds, each opening a per-feed log at init (configure_logging).
# Use a throwaway temp dir so the suite never writes feed_*.log into the repo tree.
LOGDIR = tempfile.mkdtemp(prefix="racecast-test-logs-")
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_start_from_one():
    assert m.stint_start_indices(1, 8) == (0, 1)
    assert m.stint_start_indices(1, 2) == (0, 1)
    assert m.stint_start_indices(1, 1) == (0, 1)   # B idles on the empty slot 2
    assert m.stint_start_indices(1, 0) == (0, 1)   # empty schedule, both idle


def t_takeover_midschedule():
    assert m.stint_start_indices(3, 8) == (2, 3)
    assert m.stint_start_indices(4, 8) == (3, 4)


def t_takeover_last_stint_b_idles():
    assert m.stint_start_indices(9, 8) == (7, 8)   # clamp A to last; B idles, there is no next
    assert m.stint_start_indices(8, 8) == (7, 8)   # last stint live; B idles


def t_takeover_below_one_clamps_to_one():
    assert m.stint_start_indices(0, 8) == (0, 1)
    assert m.stint_start_indices(-5, 8) == (0, 1)


class FakeSource:
    """Minimal stand-in for ScheduleSource: get/get_rows/refresh/health only."""
    def __init__(self, items): self.items = list(items)
    def get(self): return self.items
    def get_rows(self): return [(u, "", "", i + 1) for i, u in enumerate(self.items)]
    def refresh(self, timeout=None): pass
    def health(self): return {"ok": True}


URLS8 = [f"https://www.youtube.com/watch?v=stint{i}" for i in range(1, 9)]


def t_relay_default_start_unchanged():
    r = m.Relay(FakeSource(URLS8), [53001, 53002], LOGDIR)
    assert (r.A.idx, r.B.idx) == (0, 1)


def t_relay_start_stint_positions_feeds():
    r = m.Relay(FakeSource(URLS8), [53001, 53002], LOGDIR, start_stint=3)
    assert (r.A.idx, r.B.idx) == (2, 3)          # A on air with stint 3, B preloads 4


def t_relay_start_stint_clamped():
    r = m.Relay(FakeSource(URLS8[:2]), [53001, 53002], LOGDIR, start_stint=9)
    assert (r.A.idx, r.B.idx) == (1, 2)           # A clamped to last (idx 1); B idles past end


def t_set_stint_repositions_both_feeds():
    r = m.Relay(FakeSource(URLS8), [53001, 53002], LOGDIR)
    st = r.set_stint(5)
    assert (r.A.idx, r.B.idx) == (4, 5)
    assert st["feeds"]["A"]["stint"] == 5 and st["feeds"]["B"]["stint"] == 6


def t_set_stint_endpoint_http():
    # Full round-trip through the control server (ephemeral port; feeds not started).
    r = m.Relay(FakeSource(URLS8), [53001, 53002], LOGDIR)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), m.make_handler(r))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        st = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/set/stint/3", timeout=5).read())
    finally:
        srv.shutdown()
    assert st["feeds"]["A"]["stint"] == 3 and st["feeds"]["B"]["stint"] == 4
    assert (r.A.idx, r.B.idx) == (2, 3)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

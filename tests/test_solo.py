#!/usr/bin/env python3
"""Stdlib unit checks for solo relay mode (#302). Run: python3 tests/test_solo.py"""
import importlib.util, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOGDIR = tempfile.mkdtemp(prefix="racecast-test-solo-")
spec = importlib.util.spec_from_file_location(
    "irofeeds_solo", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def _solo_relay():
    return m.Relay(None, [], LOGDIR, solo=True, sheet_id="abc", league_name="Solo")


def t_solo_relay_has_no_feeds():
    r = _solo_relay()
    assert r.solo is True
    assert r.feeds == {}
    assert r.race_source is None and r.qual_source is None
    assert r.pov is None                      # no pov_source passed


def t_endurance_relay_still_has_ab_and_solo_false():
    class _Src:
        def get(self): return ["u1", "u2"]
        def get_rows(self): return [("u1", "", "", 1), ("u2", "", "", 2)]
        def refresh(self, timeout=None): pass
        def health(self): return {"ok": True}
    r = m.Relay(_Src(), [53001, 53002], LOGDIR)
    assert r.solo is False
    assert set(r.feeds) == {"A", "B"}


def t_solo_status_is_feedless_and_shaped():
    r = _solo_relay()
    s = r.status()
    assert s["mode"] == "solo" and s["solo"] is True
    assert s["feeds"] == {}
    assert s["live"] == {"feed": None, "stint": None, "mode": "solo"}
    assert s["league"]["sheet_id"] == "abc"
    assert "health" in s and "obs" in s


def t_solo_feed_controls_are_guarded_not_crashing():
    r = _solo_relay()
    assert r.live_feed() is None
    assert r.on_air_row_idx() == 0
    assert r.live_row_map() == {}
    assert r.live_schedule_row() is None
    for call in (r.next_auto, r.reload, lambda: r.set_stint(2),
                 lambda: r.set_mode("qualifying")):
        out = call()
        assert out.get("solo") is True and "error" in out


def t_solo_heartbeat_paths_never_crash():
    import time as _t
    r = _solo_relay()
    now = _t.time()
    # the heartbeat body constituents must not raise in solo, where there are no A/B feeds
    r._sample_connectivity()
    r._refresh_health(now)
    snap = r._health_snapshot(now)          # feed fields NULL, POV/system fields present
    assert snap["feed_a_state"] is None and snap["feed_b_state"] is None
    assert snap["live_feed"] is None
    r.auto_failover = True                   # even opted-in, solo must early-return
    r._maybe_auto_failover(now)              # must not KeyError on feeds[None]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

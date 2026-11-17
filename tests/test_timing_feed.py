#!/usr/bin/env python3
"""The public timing page's tick body and upload budget (#1128).
Run: python3 tests/test_timing_feed.py"""
import gzip, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import timing_feed as tf  # noqa: E402


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def t_one_build_per_tick():
    clock, calls = _Clock(), []
    body = tf.TickBody(lambda: calls.append(1) or {"v": 1}, clock=clock)
    for _ in range(50):
        body.get()
    assert len(calls) == 1, f"every viewer in one tick shares one build: {len(calls)}"
    clock.t += 2.0
    body.get()
    assert len(calls) == 2, "the next tick builds again"


def t_etag_stable_for_same_content_and_changes_with_it():
    clock, data = _Clock(), {"v": 1, "entries": [{"pos": 1}]}
    body = tf.TickBody(lambda: data, clock=clock)
    etag, raw, gz = body.get()
    assert gzip.decompress(gz) == raw and json.loads(raw) == data
    clock.t += 2.0
    assert body.get()[0] == etag, "unchanged content keeps its ETag across ticks"
    data = {"v": 1, "entries": [{"pos": 2}]}
    clock.t += 2.0
    assert body.get()[0] != etag, "changed content gets a new ETag"


def t_qualifying_and_empty_payloads_pass():
    for payload in ({"v": 1, "mode": "race", "entries": []},
                    {"v": 1, "mode": "qualifying", "qualifying": [{"pos": 1, "best_ms": 90123}]}):
        _etag, raw, _gz = tf.TickBody(lambda p=payload: p, clock=_Clock()).get()
        assert json.loads(raw.decode("utf-8")) == payload


def t_interval_is_minimum_when_idle():
    assert tf.Budget(1000, clock=_Clock()).interval() == 2.0


def t_interval_grows_with_viewers():
    clock = _Clock()
    b = tf.Budget(1000, clock=clock)
    for i in range(5000):
        clock.t = 1000.0 + i * 0.002
        b.served(700, 2.0)
    assert b.interval() == 6.0, f"1000 viewers x 700 B on 1000 kbit/s: {b.interval()}"


def t_interval_clamps_and_recovers():
    clock = _Clock()
    b = tf.Budget(50, clock=clock)
    for _ in range(2000):
        b.served(5000, 2.0)
    assert b.interval() == tf.MAX_INTERVAL_S, b.interval()
    clock.t += 11.0
    assert b.interval() == tf.MIN_INTERVAL_S, "the interval falls back once the crowd leaves"


def t_budget_kbps_parses_and_clamps():
    cases = {None: 1000, "x": 1000, "10": 50, "999999": 100000, "2500": 2500}
    for raw, want in cases.items():
        assert tf.budget_kbps(raw) == want, (raw, tf.budget_kbps(raw))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

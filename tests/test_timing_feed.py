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
    etag, raw, gz, _gz_etag = body.get()
    assert gzip.decompress(gz) == raw and json.loads(raw) == data
    clock.t += 2.0
    assert body.get()[0] == etag, "unchanged content keeps its ETag across ticks"
    data = {"v": 1, "entries": [{"pos": 2}]}
    clock.t += 2.0
    assert body.get()[0] != etag, "changed content gets a new ETag"


def t_qualifying_and_empty_payloads_pass():
    for payload in ({"v": 1, "mode": "race", "entries": []},
                    {"v": 1, "mode": "qualifying", "qualifying": [{"pos": 1, "best_ms": 90123}]}):
        raw = tf.TickBody(lambda p=payload: p, clock=_Clock()).get()[1]
        assert json.loads(raw.decode("utf-8")) == payload


def t_interval_is_minimum_when_idle():
    assert tf.Budget(1000, clock=_Clock()).interval() == 2.0


def t_interval_grows_with_viewers():
    clock = _Clock()
    b = tf.Budget(1000, clock=clock, window_s=10.0)
    for i in range(5000):
        clock.t = 1000.0 + i * 0.002
        b.served(700, 2.0)
    assert b.interval() == 6.0, f"1000 viewers x 700 B on 1000 kbit/s: {b.interval()}"


def t_interval_clamps_and_recovers():
    clock = _Clock()
    b = tf.Budget(50, clock=clock, window_s=10.0)
    for _ in range(2000):
        b.served(5000, 2.0)
    assert b.interval() == tf.MAX_INTERVAL_S, b.interval()
    clock.t += 11.0
    assert b.interval() == tf.MIN_INTERVAL_S, "the interval falls back once the crowd leaves"


def t_budget_kbps_parses_and_clamps():
    cases = {None: 1000, "x": 1000, "10": 50, "999999": 100000, "2500": 2500}
    for raw, want in cases.items():
        assert tf.budget_kbps(raw) == want, (raw, tf.budget_kbps(raw))



def t_gzip_only_when_accepted():
    for header, want in (("gzip, deflate, br", True), ("br;q=1.0, gzip;q=0.8", True), ("GZIP", True),
                         ("gzip;q=0", False), ("gzip; q=0.000", False), ("identity", False), (None, False),
                         ("*", True), ("*;q=0", False), ("x-gzip-ish", False)):
        assert tf.accepts_gzip(header) is want, (header, want)


def t_etag_matching_follows_if_none_match():
    e, g = '"abc"', '"abc-gz"'
    for header, want in ((e, True), (g, True), ('W/"abc"', True), ('"x", "abc-gz"', True), ("*", True),
                         ('"x"', False), (None, False), ("", False)):
        assert tf.etag_matches(header, e, g) is want, (header, want)


def t_gzip_and_plain_bodies_have_their_own_etags():
    etag, raw, gz, gz_etag = tf.encode(b"{}")
    assert etag != gz_etag and gz_etag.startswith(etag[:-1]), (etag, gz_etag)


def t_a_failing_build_keeps_the_last_body_and_retries_once_per_tick():
    clock, calls, fail = _Clock(), [], [False]

    def build():
        calls.append(1)
        if fail[0]:
            raise RuntimeError("boom")
        return {"v": 1}
    body = tf.TickBody(build, clock=clock)
    first = body.get()
    fail[0] = True
    clock.t += 2.0
    assert body.get() == first and body.get() == first, "viewers keep the last good body"
    assert len(calls) == 2, f"one attempt per tick, not per request: {len(calls)}"
    try:
        tf.TickBody(build, clock=_Clock()).get()
        raise AssertionError("no body yet: the error must reach the caller")
    except RuntimeError:
        pass


def t_the_body_reports_its_age():
    clock = _Clock()
    body = tf.TickBody(lambda: {"v": 1}, clock=clock)
    body.get()
    clock.t += 1.5
    assert body.age() == 1.5, body.age()

def _obeying_crowd(viewers, seconds, kbps=1000, nbytes=950):
    """Clients that poll again after the interval they were handed; returns kbit/s per 10 s."""
    import heapq, random
    rnd = random.Random(7)
    clock = _Clock()
    b = tf.Budget(kbps, clock=clock)
    due = [(clock.t + rnd.uniform(0, 30), i) for i in range(viewers)]
    heapq.heapify(due)
    end, buckets = clock.t + seconds, {}
    while due and due[0][0] < end:
        clock.t, i = heapq.heappop(due)
        interval = b.interval()
        b.served(nbytes, interval)
        k = int((clock.t - 1000.0) // 10)
        buckets[k] = buckets.get(k, 0) + nbytes
        heapq.heappush(due, (clock.t + interval + rnd.uniform(-0.5, 0.5), i))
    return [v * 8 / 1000 / 10 for k, v in sorted(buckets.items()) if k >= 12]


def t_an_obeying_crowd_stays_near_the_budget():
    for viewers in (500, 1500, 2500):
        rates = _obeying_crowd(viewers, 400)
        need = min(1000, viewers * 950 * 8 / 1000 / tf.MIN_INTERVAL_S)
        assert max(rates) < need * 1.3 and min(rates) > need * 0.6, \
            f"{viewers} viewers swing between {min(rates):.0f} and {max(rates):.0f} kbit/s"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

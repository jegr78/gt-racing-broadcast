#!/usr/bin/env python3
"""Stdlib checks for the relay restart soak's pure analysis (#619).
Run: python3 tests/test_restart_soak.py"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import restart_soak as m   # noqa: E402

RESERVE = 3.0              # RACECAST_FEED_PREBUFFER_S on the measured hosts


def _s(t, backlog=4.0, snaps=0):
    return {"t": float(t), "backlog_s": backlog, "snaps": snaps}


def _run(pairs, snaps=0):
    return [_s(t, b, snaps) for t, b in pairs]


# --------------------------------------------------------------------------
# baseline: the floor, because the raw value is a sawtooth
# --------------------------------------------------------------------------
def t_baseline_is_the_floor_of_the_window_not_its_mean():
    # On 5 s HLS segments backlog_s saws between the reserve and the next segment; the
    # floor is the part a slow consumer pushes up (#583). A mean would import the saw.
    saw = _run([(0, 4.0), (10, 8.5), (20, 3.8), (30, 8.9), (40, 3.9)])
    assert m.baseline_before(saw, 50.0) == 3.8
    # Only the preceding window counts, so an earlier era cannot set today's baseline.
    assert m.baseline_before(_run([(0, 1.0), (70, 4.0), (80, 4.2)]), 90.0) == 4.0
    assert m.baseline_before([], 50.0) is None
    assert m.baseline_before([{"t": 1.0, "backlog_s": None}], 50.0) is None


# --------------------------------------------------------------------------
# one restart
# --------------------------------------------------------------------------
def t_baseline_does_not_reach_back_into_the_previous_restart():
    # Found by a live two-minute-cadence run on the production host, not by reading code.
    # Right after a rejoin the backlog dips to 0.0: OBS sits at the live edge with
    # nothing buffered yet. A 60 s floor that swallowed that dip called 0.0 the settled
    # state, and then nothing could come back to "within one reserve of 0.0". The second
    # restart was reported FAIL although it had recovered exactly like the first.
    after_a_restart = _run([(100, 0.0), (110, 1.8), (150, 2.4), (170, 2.5), (190, 2.6)])
    assert m.baseline_before(after_a_restart, 200.0) == 2.4, "unclamped, the dip is inside"
    assert m.baseline_before(after_a_restart, 200.0, not_before=160.0) == 2.5
    # summarize clamps each baseline to after the previous restart's deadline.
    s = m.summarize(_run([(0, 2.5), (60, 2.6), (100, 4.4), (112, 0.0), (140, 1.4),
                          (240, 2.5), (244, 4.2), (256, 2.6), (300, 2.5)]),
                    [100.0, 244.0], RESERVE)
    assert s["restarts"][1]["baseline_s"] == 2.5, s["restarts"][1]
    assert s["restarts"][1]["recovered"] is True, s["restarts"][1]


def t_recovery_needs_the_backlog_to_STAY_down_not_just_touch_baseline():
    # The failure this guards is the one #619 is about: a rejoin drops the backlog, and
    # then it climbs again. A first-crossing test would call that a recovery and the
    # soak would pass on exactly the shape it exists to catch.
    bounced = _run([(100, 30.0), (110, 4.5), (120, 12.0), (130, 20.0), (150, 28.0)])
    peak, after, ok = m.recovery(bounced, 100.0, 4.0, RESERVE)
    assert peak == 30.0
    assert ok is False, "a dip that does not hold is not a recovery"
    assert after is None
    # A real recovery: down and staying down. It counts from the FIRST sample inside the
    # reserve that holds, so t=110 at 6.0 (limit 4.0 + 3.0) is the moment, not the later
    # 4.2. Anything else would report the settling time of the noise, not of the rejoin.
    healed = _run([(100, 30.0), (110, 6.0), (120, 4.2), (130, 4.3), (150, 4.1)])
    peak, after, ok = m.recovery(healed, 100.0, 4.0, RESERVE)
    assert (peak, after, ok) == (30.0, 10.0, True)


def t_recovery_is_unknown_rather_than_failed_when_nothing_was_measured():
    # A soak that measured nothing must not read as a failure any more than as a pass.
    assert m.recovery([], 100.0, 4.0, RESERVE) == (None, None, None)
    assert m.recovery(_run([(100, 5.0)]), 100.0, None, RESERVE) == (None, None, None)
    assert m.recovery(_run([(100, 5.0)]), 100.0, 4.0, None) == (None, None, None)
    # Samples exist but all lie beyond the deadline: nothing to judge inside it.
    late = _run([(100 + m.RECOVERY_DEADLINE_S + 10, 4.0)])
    assert m.recovery(late, 100.0, 4.0, RESERVE) == (None, None, None)


def t_recovery_allows_exactly_one_reserve_above_the_baseline():
    # The reserve is what the relay deliberately holds back, so it is the unit for
    # "back where it was". At the limit it counts as recovered; a hair above does not.
    at = _run([(100, 20.0), (110, 7.0), (120, 7.0)])
    assert m.recovery(at, 100.0, 4.0, RESERVE)[2] is True      # 7.0 == 4.0 + 3.0
    over = _run([(100, 20.0), (110, 7.1), (120, 7.1)])
    assert m.recovery(over, 100.0, 4.0, RESERVE)[2] is False


# --------------------------------------------------------------------------
# the whole run
# --------------------------------------------------------------------------
def t_drift_compares_two_halves_and_refuses_a_trend_from_two_points():
    def r(*vals):
        return [{"baseline_s": v} for v in vals]
    # backlog_s saws with the segment cadence, so two endpoints measure where the saw
    # happened to be. A live five-minute run with two restarts reported 3.2 s of "drift"
    # between baselines of 1.4 and 4.6, both the same healthy state. Two points are not
    # a trend, so the amount is reported and the judgement is withheld.
    assert m.drift(r(1.4, 4.6), RESERVE) == (3.2, None)
    # Four points carry two halves, which is the fewest that can.
    assert m.drift(r(4.0, 4.2, 4.6, 5.0), RESERVE) == (0.7, True)
    creep = r(4.0, 4.4, 12.0, 14.0)
    assert m.drift(creep, RESERVE) == (8.8, False)
    # A run that ends lower is information, not a failure.
    assert m.drift(r(9.0, 9.0, 4.0, 4.0), RESERVE) == (-5.0, True)
    assert m.drift(r(4.0), RESERVE) == (None, None)
    assert m.drift([{"baseline_s": None}, {"baseline_s": None}], RESERVE) == (None, None)


def t_restart_observed_watches_the_state_age_not_the_state_string():
    # The hole this closes: a soak that triggers nothing passes, because every window
    # sits at its baseline. Feed.reload() kills the streamlink process today, so the
    # request is a real restart; if that ever changes, the run must say UNKNOWN.
    # The signal is state_age_s falling. A re-serve took 4.3 to 5.5 s on both measured
    # hosts while the soak samples every 10 s, so the `connecting` state is easy to
    # sample straight past; the age resets whatever the cadence.
    def a(*pairs):
        return [{"t": float(t), "age_s": age} for t, age in pairs]
    restarted = a((80, 300.0), (90, 310.0), (100, 2.0), (110, 12.0))
    assert m.restart_observed(restarted, 100.0) is True
    nothing = a((80, 300.0), (90, 310.0), (100, 320.0), (110, 330.0))
    assert m.restart_observed(nothing, 100.0) is False, "an age that only grows is no restart"
    assert m.restart_observed([], 100.0) is None
    assert m.restart_observed(a((110, 2.0)), 100.0) is None      # nothing before it
    assert m.restart_observed([{"t": 90.0, "age_s": None}], 100.0) is None


def t_verdict_of_a_restart_that_never_happened_is_unknown_not_pass():
    # A relay that ignored every /reload would otherwise produce a flat, beautiful run.
    flat = [{"t": float(t), "backlog_s": 4.0, "snaps": 0, "age_s": 300.0 + t}
            for t in (0, 60, 100, 140)]
    s = m.summarize(flat, [100.0], RESERVE)
    assert s["restarts"][0]["observed"] is False
    state, why = m.verdict(s)
    assert state == "UNKNOWN", (state, why)
    assert "never restarted the feed" in why[0], why


def t_snap_growth_counts_ring_laps_over_the_run():
    assert m.snap_growth(_run([(0, 4.0), (10, 4.0)], snaps=0)) == 0
    lapped = [_s(0, 4.0, 2), _s(10, 4.0, 5)]
    assert m.snap_growth(lapped) == 3
    assert m.snap_growth([{"t": 0.0, "snaps": None}]) is None


def t_verdict_fails_on_each_criterion_and_says_which():
    healthy = m.summarize(_run([(0, 4.0), (60, 4.0), (100, 20.0), (110, 4.2), (160, 4.1)]),
                          [100.0], RESERVE)
    assert m.verdict(healthy) == ("PASS", [])
    bounced = m.summarize(_run([(0, 4.0), (60, 4.0), (100, 30.0), (140, 25.0)]),
                          [100.0], RESERVE)
    state, why = m.verdict(bounced)
    assert state == "FAIL" and "did not come back" in why[0], why
    lapped = m.summarize([_s(0, 4.0, 0), _s(60, 4.0, 0), _s(100, 4.0, 4)], [100.0], RESERVE)
    state, why = m.verdict(lapped)
    assert state == "FAIL" and "lapped a consumer 4 time(s)" in " ".join(why), why


def t_verdict_of_an_empty_run_is_unknown_not_pass():
    # The trap this closes: a soak whose relay was unreachable produces no samples, and
    # "no failures found" would read as a clean night.
    assert m.verdict(m.summarize([], [], RESERVE)) == ("UNKNOWN", ["nothing was measured"])
    assert m.verdict(m.summarize([], [100.0], None))[0] == "UNKNOWN"


# --------------------------------------------------------------------------
# the pure helpers the driver uses
# --------------------------------------------------------------------------
def t_sample_follows_the_on_air_feed_instead_of_a_pinned_one():
    # A handover or a takeover moves the on-air feed mid-run. Pinning A at the start
    # would quietly measure an idle feed for the rest of the soak, and an idle feed
    # answers None to everything, which the verdict would read as "nothing measured".
    st = {"live": {"feed": "B"},
          "feeds": {"A": {"state": "stopped", "backlog_s": None},
                    "B": {"state": "serving", "quality": "1080p60", "platform": "youtube",
                          "backlog_s": 4.2, "inbound_max_gap_s": 5.1, "consumer_snaps": 0}}}
    row = m.sample_of(st, 12.34)
    assert row["feed"] == "B" and row["backlog_s"] == 4.2
    assert row["gap_s"] == 5.1 and row["snaps"] == 0 and row["t"] == 12.3
    # Nothing on air, or a relay that answered garbage: a row, never an exception.
    assert m.sample_of({"live": {}, "feeds": {}}, 1.0)["backlog_s"] is None
    assert m.sample_of(None, 1.0)["feed"] is None


def t_relay_url_refuses_a_scheme_that_is_not_http():
    import argparse
    assert m.relay_url("http://100.64.0.1:8088/") == "http://100.64.0.1:8088"
    assert m.relay_url("https://host:8088") == "https://host:8088"
    for bad in ("file:///etc/passwd", "ftp://host/x", "127.0.0.1:8088", ""):
        try:
            m.relay_url(bad)
        except argparse.ArgumentTypeError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def t_render_marks_a_restart_that_did_not_come_back():
    s = m.summarize(_run([(0, 4.0), (60, 4.0), (100, 30.0), (140, 28.0)]), [100.0], RESERVE)
    text = m.render(s, *m.verdict(s))
    assert "NO" in text, text                      # the per-restart column
    assert "FAIL" in text and "did not come back" in text, text
    healthy = m.summarize(_run([(0, 4.0), (60, 4.0), (100, 20.0), (110, 4.1), (160, 4.0)]),
                          [100.0], RESERVE)
    ok_text = m.render(healthy, *m.verdict(healthy))
    assert "PASS" in ok_text and " NO" not in ok_text, ok_text
    # A missing reading prints n/a, never a number that looks measured.
    blank = m.summarize([], [100.0], RESERVE)
    assert "n/a" in m.render(blank, *m.verdict(blank))


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

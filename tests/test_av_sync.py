#!/usr/bin/env python3
"""Stdlib unit checks for the A/V sync disturbance detector.
Run: python3 tests/test_av_sync.py

Every sample line here is copied verbatim from a real OBS 32.2.2 log taken while
restarting a live feed. The parser's job is to match what OBS actually writes, so an
invented line proves nothing.
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


av = _load("av_sync", ("src", "scripts", "av_sync.py"))

REPAIR = ("22:52:21.790: Source Feed A audio is lagging (over by 5415.66 ms) "
          "at max audio buffering. Restarting source audio.")
REPAIR_SMALL = ("22:57:08.211: Source Feed A audio is lagging (over by 174.05 ms) "
                "at max audio buffering. Restarting source audio.")
REPAIR_POV = ("23:01:02.010: Source Feed POV audio is lagging (over by 12.00 ms) "
              "at max audio buffering. Restarting source audio.")
DTS_ORDER = "22:25:48.729: warning: DTS 1258128000 < 1259016000 out of order"
DTS_DISC = ("22:44:43.042: warning: DTS discontinuity in stream 0: packet 159 with "
            "DTS 1330010906, packet 160 with DTS 8920686592")
CORRUPT = "22:48:26.627: warning: Packet corrupt (stream = 1, dts = 350908500)."
NOISE = "22:10:21.725: \tpreset:       p5"


def t_parses_an_audio_repair_with_its_source_and_magnitude():
    assert av.parse_obs_log_line(REPAIR) == {
        "at": "22:52:21.790", "kind": "audio_repair", "source": "Feed A", "ms": 5415.66}


def t_a_small_repair_is_the_same_kind():
    # OBS has no millisecond threshold: the condition is audio_buffering_maxed() and
    # the number is only how far past the mix clock the source was. 174 ms and 5415 ms
    # are the same event, so neither may be filtered out here.
    ev = av.parse_obs_log_line(REPAIR_SMALL)
    assert ev is not None, "a 174 ms repair must not be filtered out"
    assert ev["kind"] == "audio_repair" and ev["ms"] == 174.05


def t_a_source_name_with_a_space_is_not_truncated():
    ev = av.parse_obs_log_line(REPAIR_POV)
    assert ev is not None, "a source name containing a space must still parse"
    assert ev["source"] == "Feed POV"


def t_dts_and_corrupt_lines_parse_but_name_no_source():
    # These carry no source name, so they can never be attributed to a feed and are
    # recorded as context only.
    for line, kind in ((DTS_ORDER, "dts_backward"), (DTS_DISC, "dts_backward"),
                       (CORRUPT, "packet_corrupt")):
        ev = av.parse_obs_log_line(line)
        assert ev["kind"] == kind, (line, ev)
        assert ev["source"] is None and ev["ms"] is None


def t_a_malformed_magnitude_is_not_an_event_instead_of_an_exception():
    # [\d.]+ also matches "1.2.3", and a float() ValueError escaping the tail loop
    # lands in the handler that means "the file rotated", so lines are skipped
    # silently. A line OBS would never write must not be an event.
    for bad in ("12:34:56.789: Source Feed A audio is lagging (over by 1.2.3 ms) "
                "at max audio buffering. Restarting source audio.",
                "12:34:56.789: Source Feed A audio is lagging (over by ... ms) "
                "at max audio buffering. Restarting source audio.",
                "12:34:56.789: Source Feed A audio is lagging (over by . ms)"):
        try:
            ev = av.parse_obs_log_line(bad)
        except ValueError as exc:
            raise AssertionError(
                f"a malformed magnitude must not raise: {bad!r} -> {exc}") from None
        assert ev is None, bad


def t_record_only_mutates_and_says_so():
    # record folds the event into the state it was given and returns None, so
    # assigning its result and ignoring it are the same thing.
    st = av.new_state()
    assert av.record(st, av.parse_obs_log_line(REPAIR), now=1.0, serving_age_s=8.0) is None
    assert st["feeds"]["A"]["repairs"] == 1


def t_an_unrelated_line_is_not_an_event():
    assert av.parse_obs_log_line(NOISE) is None
    assert av.parse_obs_log_line("") is None
    assert av.parse_obs_log_line("22:00:00.000: Video stopped") is None


def t_feed_for_source():
    assert av.feed_for_source("Feed A") == "A"
    assert av.feed_for_source("Feed B") == "B"
    assert av.feed_for_source("Feed POV") == "POV"
    # The commentary mic (#593) is an OBS input too and must never be read as a feed.
    assert av.feed_for_source("Commentary Mic Device") is None
    assert av.feed_for_source(None) is None


def t_a_repair_just_after_the_feed_started_serving_is_expected():
    # Repairs land 6-10 s after the feed enters `serving`.
    st = av.new_state()
    av.record(st, av.parse_obs_log_line(REPAIR), now=1000.0, serving_age_s=8.0)
    assert st["feeds"]["A"]["repairs"] == 1
    assert st["feeds"]["A"]["unexplained"] == 0
    assert st["feeds"]["A"]["last_ms"] == 5415.66


def t_a_repair_with_no_recent_restart_is_unexplained():
    st = av.new_state()
    av.record(st, av.parse_obs_log_line(REPAIR), now=1000.0, serving_age_s=600.0)
    assert st["feeds"]["A"]["repairs"] == 1
    assert st["feeds"]["A"]["unexplained"] == 1


def t_a_feed_that_is_not_serving_cannot_have_an_expected_repair():
    # serving_age_s None = the feed is not serving. Nothing the relay did explains a
    # repair then, so it must not be waved through as expected.
    st = av.new_state(); av.record(st, av.parse_obs_log_line(REPAIR),
                   now=1000.0, serving_age_s=None)
    assert st["feeds"]["A"]["unexplained"] == 1


def t_an_unattributed_event_is_counted_without_inventing_a_feed():
    st = av.new_state()
    av.record(st, av.parse_obs_log_line(DTS_ORDER), now=1.0, serving_age_s=None)
    av.record(st, av.parse_obs_log_line(CORRUPT), now=2.0, serving_age_s=None)
    assert st["feeds"] == {}
    assert st["context"] == {"dts_backward": 1, "packet_corrupt": 1}


def t_six_repairs_within_a_fifth_of_a_second_count_six_times():
    # OBS logs these about 170 ms apart; collapsing them would hide how hard a
    # restart hit.
    st = av.new_state()
    for i in range(6):
        av.record(st, av.parse_obs_log_line(REPAIR_SMALL),
                       now=1000.0 + i * 0.02, serving_age_s=14.0)
    assert st["feeds"]["A"]["repairs"] == 6 and st["feeds"]["A"]["unexplained"] == 0


def t_status_block_is_empty_until_something_happens():
    assert av.status_block(av.new_state(), now=10.0) == {}


def t_status_block_reports_per_feed_counts_and_the_last_magnitude():
    st = av.new_state(); av.record(st, av.parse_obs_log_line(REPAIR),
                   now=1000.0, serving_age_s=8.0)
    blk = av.status_block(st, now=1030.0)
    assert blk["A"] == {"repairs": 1, "unexplained": 0, "last_ms": 5415.66,
                        "last_age_s": 30.0, "last_at": "22:52:21.790"}


def t_only_an_unexplained_repair_becomes_a_health_fact():
    st = av.new_state(); av.record(st, av.parse_obs_log_line(REPAIR),
                   now=1000.0, serving_age_s=8.0)
    assert av.health_fact(st, now=1005.0) == {}
    av.record(st, av.parse_obs_log_line(REPAIR), now=1100.0, serving_age_s=600.0)
    assert av.health_fact(st, now=1105.0) == {"A": 5415.66}


def t_the_health_fact_reports_the_unexplained_repair_not_the_latest_one():
    # The fact must carry the UNEXPLAINED repair's magnitude. Reporting the latest one
    # instead names a later, expected event, which is the mis-attribution this detector
    # exists to avoid.
    st = av.new_state()
    av.record(st, {"at": "23:49:03.732", "kind": "audio_repair",
                        "source": "Feed A", "ms": 987.3},
                   now=1000.0, serving_age_s=600.0)          # unexplained
    av.record(st, {"at": "23:49:55.528", "kind": "audio_repair",
                        "source": "Feed A", "ms": 996.79},
                   now=1052.0, serving_age_s=22.0)           # expected, right after a restart
    assert st["feeds"]["A"] == {"repairs": 2, "unexplained": 1, "last_ms": 996.79,
                                "last_ts": 1052.0, "last_at": "23:49:55.528",
                                "last_unexplained_ts": 1000.0,
                                "last_unexplained_ms": 987.3}
    assert av.health_fact(st, now=1060.0) == {"A": 987.3}


def t_the_health_fact_ages_out_so_one_blip_does_not_stay_yellow_all_event():
    st = av.new_state(); av.record(st, av.parse_obs_log_line(REPAIR),
                   now=1000.0, serving_age_s=600.0)
    assert av.health_fact(st, now=1000.0 + av.HEALTH_HOLD_S - 1) == {"A": 5415.66}
    assert av.health_fact(st, now=1000.0 + av.HEALTH_HOLD_S + 1) == {}


def t_the_window_covers_the_slowest_restart_actually_measured():
    # The classification hinges on this one number. Three legs stand between a feed
    # serving and OBS being ABLE to log a repair: the relay's prefetch wait (up to
    # 7 s), OBS's reconnect_delay_sec (10 s), and OBS filling buffering_mb (about 9 s
    # at 7.2 Mbps). That is a 26 s floor. The slowest repair measured was 32 s, which
    # a first attempt at 30 s wrongly called unexplained.
    assert av.RESTART_WINDOW_S >= 26.0, "below the floor the reconnect path alone needs"
    assert av.RESTART_WINDOW_S >= 32.0, "below the slowest repair actually observed"


def t_the_slowest_measured_restart_is_classified_as_expected():
    # The 32 s case a 30 s window wrongly called unexplained.
    st = av.new_state(); av.record(st, av.parse_obs_log_line(REPAIR),
                   now=1000.0, serving_age_s=32.0)
    assert st["feeds"]["A"]["unexplained"] == 0
    assert av.health_fact(st, now=1001.0) == {}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""Unit checks for the pure post-event report builder (stdlib only)."""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# report_build imports health_store by module name, so put src/scripts on sys.path.
import sys
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
rb = _load("report_build", ("src", "scripts", "report_build.py"))

import re
import xml.etree.ElementTree as ET

sys.path.insert(0, HERE)
import test_report_telemetry as trt


def _sample(ts, **kw):
    row = {"ts": ts, "kind": "periodic", "health_level": "green",
           "health_reasons": [], "feed_a_down": 0, "feed_b_down": 0,
           "live_stint": None, "live_feed": None}
    row.update(kw)
    return row


def t_select_session_single_run():
    ts = [100.0, 130.0, 160.0, 190.0]
    assert rb.select_session(ts, gap_s=1800) == (100.0, 190.0)


def t_select_session_splits_on_big_gap():
    # two runs separated by a 2000s gap (> SESSION_GAP_S) -> only the last run
    ts = [100.0, 130.0, 5000.0, 5030.0, 5060.0]
    assert rb.select_session(ts, gap_s=1800) == (5000.0, 5060.0)


def t_select_session_handover_gap_stays_one_session():
    # a 4-min handover gap (< 1800s) does NOT split the event
    ts = [100.0, 130.0, 400.0, 430.0]
    assert rb.select_session(ts, gap_s=1800) == (100.0, 430.0)


def t_select_session_empty():
    assert rb.select_session([], gap_s=1800) == (None, None)


def t_select_session_floor_discards_earlier_event():
    # a previous event (100-130) then this one (400-430): a 270s gap < 1800s would
    # normally merge them into one window, so a floor at this event's start clamps it
    ts = [100.0, 130.0, 400.0, 430.0]
    assert rb.select_session(ts, gap_s=1800, floor=400.0) == (400.0, 430.0)
    assert rb.select_session(ts, gap_s=1800, floor=350.0) == (400.0, 430.0), \
        "floor between samples keeps only those at/after it"


def t_select_session_floor_none_is_unchanged():
    ts = [100.0, 130.0, 400.0, 430.0]
    assert rb.select_session(ts, gap_s=1800, floor=None) == (100.0, 430.0)


def t_select_session_floor_after_all_samples_empty():
    ts = [100.0, 130.0]
    assert rb.select_session(ts, gap_s=1800, floor=999.0) == (None, None)


def t_quality_includes_host_metrics():
    # Host CPU, RAM and network surface in the report quality section. (#536)
    samples = [_sample(0.0, sys_cpu_pct=20.0, sys_mem_pct=60.0,
                       sys_net_down_kbps=3000.0, sys_net_up_kbps=10000.0),
               _sample(30.0, sys_cpu_pct=40.0, sys_mem_pct=70.0,
                       sys_net_down_kbps=1000.0, sys_net_up_kbps=12000.0)]
    q = rb._quality(samples)
    assert q["sys_cpu_avg"] == 30.0 and q["sys_cpu_peak"] == 40.0, q
    assert q["sys_mem_avg"] == 65.0 and q["sys_mem_peak"] == 70.0, q
    # kbps -> Mbps conversion
    assert q["net_down_avg"] == 2.0 and q["net_down_peak"] == 3.0, q
    assert q["net_up_avg"] == 11.0 and q["net_up_peak"] == 12.0, q
    html = rb.render_html(rb.build_report(samples, [], {}, "E", (0.0, 30.0), now=1.0, host="BOX"))
    assert "Host CPU (%)" in html and "Host RAM (%)" in html, "host rows missing"
    assert "Net down (Mbps)" in html and "Net up (Mbps)" in html, "net rows missing"


def t_quality_host_absent_degrades_to_dashes():
    # OBS-only samples, with no sys_*, still build; host cells fall back to a dash.
    samples = [_sample(0.0, obs_cpu_pct=5.0), _sample(30.0, obs_cpu_pct=6.0)]
    q = rb._quality(samples)
    assert q["sys_cpu_avg"] is None and q["net_down_avg"] is None, q


def t_quality_includes_inbound_gap_peak():
    # The worst inbound inter-arrival gap across both feeds surfaces as a peak row. (#535)
    samples = [_sample(0.0, feed_a_max_gap_s=0.5, feed_b_max_gap_s=0.0),
               _sample(30.0, feed_a_max_gap_s=3.4, feed_b_max_gap_s=2.1)]
    q = rb._quality(samples)
    assert q["inbound_gap_peak"] == 3.4, q            # worst gap across A and B
    html = rb.render_html(rb.build_report(samples, [], {}, "E", (0.0, 30.0), now=1.0, host="BOX"))
    assert "Max inbound gap (s)" in html, "inbound-gap row missing"


def t_build_report_includes_host():
    samples = [_sample(0.0), _sample(30.0)]
    rep = rb.build_report(samples, [], {}, "E", (0.0, 30.0), now=1.0, host="STREAM-BOX")
    assert rep["header"]["host"] == "STREAM-BOX", rep["header"]
    assert "STREAM-BOX" in rb.render_html(rep)
    # absent host degrades to empty and never appears
    rep2 = rb.build_report(samples, [], {}, "E", (0.0, 30.0), now=1.0)
    assert rep2["header"]["host"] == ""


def t_slice_log_by_window_keeps_in_window_and_continuations():
    import time as _t
    def clk(s):
        return _t.mktime(_t.strptime(s, "%Y-%m-%d %H:%M:%S"))
    text = ("2026-07-05 21:20:00 INFO before the window\n"
            "2026-07-05 21:24:40 INFO in window\n"
            "  Traceback continuation with no timestamp\n"
            "2026-07-05 21:40:00 INFO after the window\n")
    out = rb.slice_log_by_window(text, clk("2026-07-05 21:24:32"),
                                 clk("2026-07-05 21:30:11"), margin_s=0.0)
    assert "in window" in out
    assert "Traceback continuation" in out          # kept: follows an in-window line
    assert "before the window" not in out
    assert "after the window" not in out
    # no window -> unchanged
    assert rb.slice_log_by_window(text, None, None) == text


def t_slice_log_by_window_foreign_format_kept_whole():
    # a log with no parseable 'YYYY-MM-DD HH:MM:SS' prefix, such as OBS's time-only
    # form, is returned whole rather than emptied
    obs = "21:24:32.456: Loaded scene\n21:40:00.000: Something later\n"
    assert rb.slice_log_by_window(obs, 1.0, 2.0) == obs


def t_bucket_samples_collapses_concurrent():
    # two machines sampling ~same 30s window -> one row per 30s bucket (last wins)
    samples = [_sample(0.0, health_level="green"), _sample(10.0, health_level="yellow"),
               _sample(30.0, health_level="green"), _sample(40.0, health_level="red")]
    out = rb.bucket_samples(samples, bucket_s=30)
    assert [s["ts"] for s in out] == [10.0, 40.0], out


def t_build_report_uptime_and_feeds():
    samples = [_sample(0.0), _sample(30.0, health_level="yellow", feed_a_down=1),
               _sample(60.0, health_level="green"), _sample(90.0)]
    rep = rb.build_report(samples, [], {}, "Test Event", (0.0, 90.0), now=1000.0)
    assert rep["header"]["duration_s"] == 90.0
    # green for [0-30] and [60-90] = 60s of 90s -> 66.7%
    assert rep["header"]["uptime_pct"] == 66.7, rep["header"]
    feed_a = next(f for f in rep["feeds"] if f["feed"] == "A")
    assert feed_a["drops"] == 1, rep["feeds"]


def t_build_report_on_air_names_and_fallback():
    samples = [_sample(0.0, live_stint=1), _sample(30.0, live_stint=1),
               _sample(60.0, live_stint=2)]
    rep = rb.build_report(samples, [], {1: "Alice", 2: "Bob"},
                          "E", (0.0, 60.0), now=1000.0)
    names = {c["name"]: c["seconds"] for c in rep["on_air"]["commentators"]}
    assert names.get("Alice") == 60.0, rep["on_air"]
    assert "Bob" in names, rep["on_air"]
    assert rep["on_air"]["resolved"] is True
    # empty map -> "Stint N" fallback + resolved False
    rep2 = rb.build_report(samples, [], {}, "E", (0.0, 60.0), now=1000.0)
    assert any(c["name"] == "Stint 1" for c in rep2["on_air"]["commentators"]), rep2
    assert rep2["on_air"]["resolved"] is False


def t_on_air_back_to_back_same_url_counts_two_stints():
    # Display-stint samples: stint 1 then stint 2, the same commentator across a
    # same-URL back-to-back, credited as two stints with the full duration. (#500)
    samples = [_sample(0.0, live_stint=1), _sample(30.0, live_stint=1),
               _sample(60.0, live_stint=2), _sample(90.0, live_stint=2)]
    rep = rb.build_report(samples, [], {1: "Alice", 2: "Alice"},
                          "E", (0.0, 90.0), now=1000.0)
    alice = next(c for c in rep["on_air"]["commentators"] if c["name"] == "Alice")
    assert alice["stints"] == 2, rep["on_air"]
    assert alice["seconds"] == 90.0, rep["on_air"]


def t_build_report_producer_handover_from_events():
    samples = [_sample(0.0), _sample(30.0)]
    events = [{"ts": 15.0, "type": "takeover", "producer": "B",
               "metadata": {"from": "A", "stint": 2}}]
    rep = rb.build_report(samples, events, {}, "E", (0.0, 30.0), now=1000.0)
    assert rep["overlap_approximate"] is True
    assert rep["producer_handovers"] == [{"ts": 15.0, "from": "A", "to": "B", "stint": 2}]


def t_build_report_quality_none_when_empty():
    samples = [_sample(0.0), _sample(30.0)]  # no v3 quality columns set
    rep = rb.build_report(samples, [], {}, "E", (0.0, 30.0), now=1000.0)
    assert rep["quality"] is None
    samples2 = [_sample(0.0, stream_kbps=6000.0), _sample(30.0, stream_kbps=4000.0)]
    rep2 = rb.build_report(samples2, [], {}, "E", (0.0, 30.0), now=1000.0)
    assert rep2["quality"]["stream_kbps_peak"] == 6000.0, rep2["quality"]


def t_render_html_is_self_contained():
    samples = [_sample(0.0, live_stint=1), _sample(30.0, live_stint=1, feed_a_down=1),
               _sample(60.0, health_level="red")]
    rep = rb.build_report(samples, [], {1: "Alice"}, "Grand Prix", (0.0, 60.0), now=1000.0)
    html = rb.render_html(rep)
    assert html.startswith("<!doctype html>")
    assert "Grand Prix" in html
    assert "Alice" in html
    # self-contained, with no external references; the marker is dotless to stay
    # clear of CodeQL's incomplete-url-substring rule
    assert "http" + "://" not in html, "external URL leaked into report"
    assert "https" + "://" not in html
    assert "Feed reliability" in html
    assert "Incident" in html


def t_render_summary_text():
    samples = [_sample(0.0), _sample(30.0)]
    rep = rb.build_report(samples, [], {}, "My Event", (0.0, 30.0), now=1000.0)
    txt = rb.render_summary_text(rep)
    assert "My Event" in txt
    assert "uptime" in txt.lower()


def t_report_filename():
    assert rb.report_filename("Grand Prix #3", "2026-07-01") == "2026-07-01-grand-prix-3.html"
    assert rb.report_filename("", "2026-07-01") == "2026-07-01-report.html"


def t_feed_stats_interval_weighted_downtime():
    # ts 0, 30, 60 with feed_a_down=0,1,0 → down band [30,60] → downtime 30s
    samples = [_sample(0.0, feed_a_down=0), _sample(30.0, feed_a_down=1),
               _sample(60.0, feed_a_down=0)]
    rep = rb.build_report(samples, [], {}, "E", (0.0, 60.0), now=1000.0)
    feed_a = next(f for f in rep["feeds"] if f["feed"] == "A")
    assert feed_a["drops"] == 1, feed_a
    assert feed_a["downtime_s"] == 30.0, feed_a
    assert feed_a["longest_outage_s"] == 30.0, feed_a


def t_fill_gaps_does_not_bridge_relay_down_gap():
    # Contiguous green [0,30], then a ~11-min relay-down hole, then green [700,730],
    # all within one session. The hole must not count as green.
    samples = [_sample(0.0), _sample(30.0),
               _sample(700.0), _sample(730.0)]
    rep = rb.build_report(samples, [], {}, "E", (0.0, 730.0), now=1000.0)
    assert rep["header"]["uptime_pct"] < 100.0, rep["header"]
    # green wall-clock is the two contiguous 30s intervals, ~60s of 730s, not the whole span
    assert rep["header"]["uptime_pct"] <= 20.0, rep["header"]


def t_report_collects_and_renders_substitutions():
    samples = [_sample(100.0), _sample(160.0)]
    events = [
        {"ts": 130.0, "type": "feed_substitution",
         "metadata": {"feed": "A", "stint": 2, "reason": "A dropped"}},
        {"ts": 150.0, "type": "feed_substitution", "metadata": {"feed": "B", "stint": 3}},
        {"ts": 140.0, "type": "takeover", "producer": "B", "metadata": {"from": "A", "stint": 2}},
    ]
    names = {2: "Ann", 3: "Bob"}   # name_for_stint is a DICT, keyed 1-based
    rep = rb.build_report(samples, events, names, "6h Spa", (100.0, 160.0), now=200.0)
    subs = rep["substitutions"]
    assert [s["feed"] for s in subs] == ["A", "B"]
    assert subs[0] == {"ts": 130.0, "feed": "A", "stint": 2, "streamer": "Ann", "reason": "A dropped"}
    assert subs[1]["streamer"] == "Bob" and subs[1]["reason"] == ""
    html = rb.render_html(rep)
    assert "Stream substitutions" in html and "Ann" in html and "A dropped" in html
    # empty case renders no section
    rep0 = rb.build_report(samples, [], {}, "", (100.0, 160.0), now=200.0)
    assert rep0["substitutions"] == []
    assert "Stream substitutions" not in rb.render_html(rep0)


def t_report_collects_and_renders_obs_consumer_events():
    # Ring laps under OBS, automatic OBS rebuilds and the guard's stand-down are facts
    # the report must show. (#582)
    samples = [_sample(100.0), _sample(160.0)]
    events = [
        {"ts": 110.0, "type": "fanout_overflow",
         "metadata": {"feed": "A", "stint": 2, "snaps": 1}},
        {"ts": 120.0, "type": "obs_rebuild",
         "metadata": {"feed": "A", "stint": 2, "stall_fraction": 0.67}},
        {"ts": 140.0, "type": "obs_rebuild_stood_down",
         "metadata": {"feed": "A", "stint": 2, "attempts": 3}},
        {"ts": 150.0, "type": "obs_rebuild_rearmed",
         "metadata": {"feed": "B", "stint": 3, "reason": "stint change"}},
        {"ts": 155.0, "type": "feed_recovery", "metadata": {"feed": "A", "stint": 2}},
    ]
    rep = rb.build_report(samples, events, {2: "Ann", 3: "Bob"}, "", (100.0, 160.0), now=200.0)
    assert rep["obs_consumer"] == [
        {"ts": 110.0, "feed": "A", "stint": 2, "streamer": "Ann",
         "what": "Ring overflow under OBS (1x)"},
        {"ts": 120.0, "feed": "A", "stint": 2, "streamer": "Ann",
         "what": "Automatic OBS rebuild (stall fraction 0.67)"},
        {"ts": 140.0, "feed": "A", "stint": 2, "streamer": "Ann",
         "what": "Auto-rebuild stood down after 3 ineffective rebuilds"},
        {"ts": 150.0, "feed": "B", "stint": 3, "streamer": "Bob",
         "what": "Auto-rebuild re-armed (stint change)"},
    ]
    html = rb.render_html(rep)
    assert "OBS consumer events" in html and "Auto-rebuild stood down" in html
    rep0 = rb.build_report(samples, [], {}, "", (100.0, 160.0), now=200.0)
    assert rep0["obs_consumer"] == []
    assert "OBS consumer events" not in rb.render_html(rep0)


def t_report_collects_and_renders_recoveries():
    # A self-healed feed drop must show in its own report section, or a short stutter
    # leaves the report reading "all green".
    samples = [_sample(100.0), _sample(160.0)]
    events = [
        {"ts": 130.0, "type": "feed_recovery",
         "metadata": {"feed": "A", "stint": 2, "downtime_s": 11.0}},
        {"ts": 150.0, "type": "feed_recovery", "metadata": {"feed": "A", "stint": 2}},
    ]
    names = {2: "Ann"}
    rep = rb.build_report(samples, events, names, "6h Spa", (100.0, 160.0), now=200.0)
    recs = rep["recoveries"]
    assert [r["feed"] for r in recs] == ["A", "A"]
    assert recs[0] == {"ts": 130.0, "feed": "A", "stint": 2, "streamer": "Ann", "downtime_s": 11.0}
    assert recs[1]["downtime_s"] == 0        # missing -> 0
    html = rb.render_html(rep)
    assert "Feed auto-recoveries" in html and "Ann" in html
    # empty case renders no section
    rep0 = rb.build_report(samples, [], {}, "", (100.0, 160.0), now=200.0)
    assert rep0["recoveries"] == []
    assert "Feed auto-recoveries" not in rb.render_html(rep0)


def t_build_report_no_mutation_on_repeated_call():
    # build_report must not mutate shared state: two calls on the same samples must
    # yield identical health_bands.
    samples = [_sample(0.0), _sample(30.0, health_level="yellow"),
               _sample(60.0, health_level="green"), _sample(90.0)]
    rep1 = rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0)
    snapshot = [dict(b) for b in rep1["health_bands"]]
    rep2 = rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0)
    assert rep1["health_bands"] == rep2["health_bands"], (
        "second call produced different health_bands", rep1["health_bands"], rep2["health_bands"])
    assert rep1["health_bands"] == snapshot, "first call's health_bands were mutated by second call"


def t_fmt_seconds():
    import time as _t
    # _fmt_dur keeps seconds even at hour scale
    assert rb._fmt_dur(3725) == "1h 2m 5s"
    assert rb._fmt_dur(125) == "2m 5s"
    assert rb._fmt_dur(5) == "5s"
    # _fmt_clock shows HH:MM:SS
    ts = _t.mktime((2026, 7, 5, 15, 17, 9, 0, 0, -1))
    assert rb._fmt_clock(ts) == "15:17:09"
    assert rb._fmt_clock(None) == "—"


def t_on_air_windows_and_exclusion():
    # part events define the on-air window; obs_stream is the fallback
    ev = [{"ts": 100, "type": "part_start", "metadata": {"index": 1}},
          {"ts": 400, "type": "part_end", "metadata": {"index": 1}}]
    assert rb.on_air_windows(ev, 500) == [(100, 400)]
    assert rb.windows_total_s([(100, 400)]) == 300
    ev2 = [{"ts": 100, "type": "obs_stream_start"}, {"ts": 300, "type": "obs_stream_stop"}]
    assert rb.on_air_windows(ev2, 500) == [(100, 300)]
    assert rb.on_air_windows([], 500) is None
    # unclosed part_start closes at session_end
    assert rb.on_air_windows([{"ts": 100, "type": "part_start"}], 500) == [(100, 500)]


def t_build_report_excludes_off_air():
    def s(ts, lvl):
        return {"ts": ts, "health_level": lvl, "health_reasons": [],
                "live_stint": 1, "feed_a_down": 0, "feed_b_down": 0}
    # off-air red before the part window must not count; in-window green = 100% uptime
    samples = [s(50, "red"), s(100, "green"), s(130, "green"), s(160, "green")]
    events = [{"ts": 100, "type": "part_start", "metadata": {"index": 1}},
              {"ts": 160, "type": "part_end", "metadata": {"index": 1}}]
    rep = rb.build_report(samples, events, {}, "T", (50, 160), 200)
    assert rep["header"]["uptime_pct"] == 100.0
    assert rep["header"]["on_air_s"] == 60
    assert rep["incidents"] == []          # the pre-window red is excluded
    # timeline lists the part boundaries
    tl = rep["broadcast_timeline"]
    assert [(r["ts"], r["label"]) for r in tl] == [(100, "Part 1 started"), (160, "Part 1 ended")]
    html = rb.render_html(rep)
    assert "Broadcast timeline" in html and "Part 1 started" in html
    assert ">On air<" in html


def t_build_report_multi_window_uptime_not_over_100():
    # A stop and restart splits the session into two on-air windows with an off-air
    # gap shorter than GAP_S between them. Health is green throughout on air, and the
    # off-air gap must not be bridged or counted as green, or a band spanning the gap
    # over-counts against on_air_s and uptime passes 100%.
    def s(ts, **kw):
        return {"ts": ts, "health_level": "green", "health_reasons": [],
                "live_stint": 1, "feed_a_down": 0, "feed_b_down": 0, **kw}
    samples = [s(100), s(130), s(160),               # window 1: 100..160 (on air)
               s(180, health_level="red"),           # off-air gap (OBS stream stopped)
               s(200), s(230), s(260)]               # window 2: 200..260 (on air)
    events = [{"ts": 100, "type": "obs_stream_start"},
              {"ts": 160, "type": "obs_stream_stop"},
              {"ts": 200, "type": "obs_stream_start"},
              {"ts": 260, "type": "obs_stream_stop"}]
    rep = rb.build_report(samples, events, {1: "Alice"}, "N24", (100, 260), now=300.0)
    assert rep["header"]["on_air_s"] == 120, rep["header"]
    # the 40s off-air gap must not inflate green past the on-air total
    assert rep["header"]["uptime_pct"] == 100.0, rep["header"]
    assert rep["incidents"] == [], rep["incidents"]     # off-air red excluded
    # commentator on-air likewise must not exceed the on-air total
    alice = next(c for c in rep["on_air"]["commentators"] if c["name"] == "Alice")
    assert alice["seconds"] <= 120, rep["on_air"]


def t_build_report_legacy_no_windows():
    # no part/obs_stream events -> whole-session behaviour (off-air counts)
    def s(ts, lvl):
        return {"ts": ts, "health_level": lvl, "health_reasons": ["off air"],
                "live_stint": 1, "feed_a_down": 0, "feed_b_down": 0}
    samples = [s(0, "green"), s(30, "red"), s(60, "green")]
    rep = rb.build_report(samples, [], {}, "T", (0, 60), 100)
    assert rep["header"]["on_air_s"] == 60          # falls back to full duration
    assert len(rep["incidents"]) == 1               # off-air still counted


def t_report_discord_fields():
    rep = {"header": {"uptime_pct": 98.0, "on_air_s": 3600, "duration_s": 7200,
                      "start": 0, "end": 7200}, "incidents": [1, 2]}
    f = dict(rb.report_discord_fields(rep))
    assert f["Uptime"] == "98.0%"
    assert f["On air"] == "1h 0m 0s"
    assert f["Incidents"] == "2"
    assert f["Session length"] == "2h 0m 0s"
    assert "Window" in f


def t_on_air_desync_seconds_from_desync_active_bands():
    # A desync_active band contributes its gap-filled duration; a clean event and
    # old samples without the key both give 0.
    samples = [_sample(0.0, live_stint=1, desync_active=1),
               _sample(30.0, live_stint=1, desync_active=1),
               _sample(60.0, live_stint=1, desync_active=0)]
    rep = rb.build_report(samples, [], {1: "Alice"}, "E", (0.0, 60.0), now=1000.0)
    # gap-filled active band [0,60] -> exactly 60.0s, because a 30->60 gap < GAP_S is bridged
    assert rep["on_air"]["desync_seconds"] == 60.0, rep["on_air"]

    clean = [_sample(0.0, live_stint=1), _sample(30.0, live_stint=1)]
    rep2 = rb.build_report(clean, [], {1: "Alice"}, "E", (0.0, 30.0), now=1000.0)
    assert rep2["on_air"]["desync_seconds"] == 0, rep2["on_air"]


def t_render_html_shows_desync_caveat_when_present():
    samples = [_sample(0.0, live_stint=1, desync_active=1),
               _sample(30.0, live_stint=1, desync_active=1),
               _sample(60.0, live_stint=1, desync_active=0)]
    rep = rb.build_report(samples, [], {1: "Alice"}, "GP", (0.0, 60.0), now=1000.0)
    assert "desync" in rb.render_html(rep).lower(), "desync caveat missing"
    # Clean event -> no desync caveat.
    clean = [_sample(0.0, live_stint=1), _sample(30.0, live_stint=1)]
    rep2 = rb.build_report(clean, [], {1: "Alice"}, "GP", (0.0, 30.0), now=1000.0)
    assert "desync" not in rb.render_html(rep2).lower()


def t_timeline_prefers_event_label_over_part_index():
    # The relay stores the real part label, so a qualifying part is "Q started" or
    # "Q ended" while metadata.index is only the pointer position. broadcast_timeline
    # must show the label, not "Part 1". (#523)
    events = [{"ts": 100, "type": "part_start", "label": "Q started",
               "metadata": {"index": 1}},
              {"ts": 160, "type": "part_end", "label": "Q ended",
               "metadata": {"index": 1}}]
    tl = rb.broadcast_timeline(events)
    assert [(r["ts"], r["label"]) for r in tl] == [(100, "Q started"), (160, "Q ended")]
    # A labelless part event still falls back to "Part {index}".
    bare = [{"ts": 50, "type": "part_start", "metadata": {"index": 2}}]
    assert rb.broadcast_timeline(bare) == [{"ts": 50, "label": "Part 2 started"}]


LINE = "Best lap 0:43.810 (theoretical 0:40.000), 3 laps, Suzuka Circuit"


def _solo_report(*extra, **idx_kw):
    idx = dict(trt.session_index(), **idx_kw)
    tel = trt.rtel.telemetry_block([idx, *extra], trt.WINDOW)
    return rb.build_report([_sample(0.0), _sample(30.0)], [], {}, "Solo", (0.0, 30.0),
                           now=1000.0, telemetry=tel)


def t_telemetry_section_renders_figures_trend_map_and_laps():
    html = rb.render_html(_solo_report())
    assert "<h2>Telemetry</h2>" in html
    for text in ("0:43.810", "0:40.000", "± 0.561 s", "2.10 L", "3 of 4",
                 "Suzuka Circuit - Full Course", "Porsche 911 RSR", "82.0", "pit",
                 "not counted", "200 m mini-sectors", "+0.762 s", "<th>#</th>"):
        assert text in html, text
    svgs = re.findall(r"<svg\b.*?</svg>", html, re.S)
    assert len(svgs) == 3, "health strip, lap trend, track map"
    for svg in svgs:
        ET.fromstring(svg)
    assert html.index("<h2>Telemetry</h2>") < html.index("<h2>Feed reliability</h2>")
    assert "may be missing" not in html


def t_telemetry_tables_scroll_inside_the_card():
    html = rb.render_html(_solo_report())
    tele = html[html.index("<h2>Telemetry</h2>"):html.index("<h2>Feed reliability</h2>")]
    assert tele.count("<div style='overflow-x:auto'><table>") == 2, \
        "the tyre and lap tables scroll on a phone instead of widening the page"


def t_best_lap_kpi_names_the_driving_order_number():
    html = rb.render_html(_solo_report())
    assert "#3, lap 3" in html, "lap numbers repeat across sessions, the driving order does not"
    assert "20261007-200000" not in html, "a single-recording group does not repeat its stem"


def t_best_lap_kpi_names_the_recording_for_a_multi_recording_group():
    second = {"rec": "20261007-210000", "start_ts": 1500.0, "end_ts": 1600.0,
              "laps": [trt._lap(1, 5.0, trt._trace(50.0, 40.0), rec="20261007-210000")]}
    html = rb.render_html(_solo_report(second))
    assert "#3, lap 3, 20261007-200000" in html, \
        "pooled across recordings, the KPI names which one set the best lap"


def t_telemetry_intro_pluralizes_laps_and_recordings():
    one = rb.render_html(_solo_report(laps=trt.session_index()["laps"][3:]))
    assert "1 lap from the GT7 telemetry recording" in one
    assert "1 laps" not in one
    other = {"rec": "20261007-210000", "start_ts": 1500.0, "laps": [trt._lap(
        1, 5.0, trt._trace(45.0, 45.0), track_id=None, car_id=1234, car="Mazda Roadster",
        rec="20261007-210000")]}
    multi = rb.render_html(_solo_report(other))
    assert "from the GT7 telemetry recordings" in multi
    single = rb.render_html(_solo_report())
    assert "from the GT7 telemetry recording," in single and "recordings" not in single


def t_unknown_track_heading_names_recording_and_session():
    other = {"rec": "20261007-210000", "start_ts": 1500.0, "laps": [trt._lap(
        1, 5.0, trt._trace(45.0, 45.0), track_id=None, car_id=1234, car="Mazda Roadster",
        rec="20261007-210000", session=2)]}
    html = rb.render_html(_solo_report(other))
    assert "<h3>Unknown track · Mazda Roadster · 20261007-210000, session 2</h3>" in html, \
        "groups on an unknown track are per GT7 session, so the heading says which"
    assert "<h3>Suzuka Circuit - Full Course · Porsche 911 RSR</h3>" in html


def t_telemetry_section_absent_without_recordings():
    rep = rb.build_report([_sample(0.0), _sample(30.0)], [], {}, "Endurance", (0.0, 30.0),
                          now=1000.0)
    assert rep["telemetry"] is None
    assert "<h2>Telemetry</h2>" not in rb.render_html(rep)
    assert "Best lap" not in rb.render_summary_text(rep)
    assert "Telemetry" not in dict(rb.report_discord_fields(rep))


def t_summary_and_discord_carry_the_best_lap_line():
    rep = _solo_report()
    assert "  " + LINE in rb.render_summary_text(rep).splitlines()
    assert dict(rb.report_discord_fields(rep))["Telemetry"] == LINE


def t_open_recording_adds_a_caveat():
    assert "may be missing" in rb.render_html(_solo_report(partial=True))


def t_discord_telemetry_field_escapes_markdown_in_the_track_name():
    idx = trt.session_index()
    odd = "Nordschleife *_~`|>[]\\ Nord"
    for lap in idx["laps"][:3]:
        lap["track"] = odd
    rep = _solo_report(**{"laps": idx["laps"]})
    line = dict(rb.report_discord_fields(rep))["Telemetry"]
    assert odd not in line, "unescaped markdown must not reach the Discord field"
    for ch in "\\*_~`|>[]":
        assert "\\" + ch in line, f"{ch!r} must be backslash-escaped"
    summary = rb.render_summary_text(rep)
    assert odd in summary, "the CLI/Control Center summary keeps the raw track name"


def t_discord_telemetry_field_capped_at_1024_chars():
    idx = trt.session_index()
    for lap in idx["laps"][:3]:
        lap["track"] = "x" * 2000
    rep = _solo_report(**{"laps": idx["laps"]})
    line = dict(rb.report_discord_fields(rep))["Telemetry"]
    assert len(line) == 1024, "Discord embed field values are capped at 1024 characters"


def t_telemetry_section_never_raises_on_a_malformed_block():
    rep = rb.build_report([_sample(0.0), _sample(30.0)], [], {}, "Broken", (0.0, 30.0),
                          now=1000.0, telemetry={"groups": [{}]})
    html = rb.render_html(rep)
    summary = rb.render_summary_text(rep)
    fields = dict(rb.report_discord_fields(rep))
    assert "Feed reliability" in html, "a broken telemetry block must not take down the rest of the report"
    assert "Telemetry could not be rendered." in html, \
        "a render failure must leave a visible caveat, not a silently dropped section"
    assert "Best lap" not in summary
    assert "Telemetry" not in fields


def t_render_summary_and_discord_survive_a_missing_telemetry_module():
    # ImportError (e.g. a frozen build missing report_telemetry) must stay inside the
    # per-surface guard, not escape render_summary_text/report_discord_fields.
    rep = _solo_report()
    had = "report_telemetry" in sys.modules
    orig = sys.modules.get("report_telemetry")
    sys.modules["report_telemetry"] = None
    try:
        summary = rb.render_summary_text(rep)
        fields = dict(rb.report_discord_fields(rep))
    finally:
        if had:
            sys.modules["report_telemetry"] = orig
        else:
            del sys.modules["report_telemetry"]
    assert "Best lap" not in summary
    assert "Telemetry" not in fields


def t_telemetry_note_escapes_lap_counts():
    tel = dict(trt.rtel.telemetry_block([trt.session_index()], trt.WINDOW))
    tel["laps_total"] = "<script>total</script>"
    tel["laps_counted"] = "<b>counted</b>"
    html = rb._telemetry_html(tel)
    assert "<script>total</script>" not in html
    assert "<b>counted</b>" not in html
    assert "&lt;script&gt;total&lt;/script&gt;" in html
    assert "&lt;b&gt;counted&lt;/b&gt;" in html


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")



# Windowed render metric, fps against the configured rate, backlog verdict. (#586)

def _broadcast(n, **kw):
    """n on-air samples, 30 s apart, on Feed A stint 1, with the given fields."""
    return [_sample(i * 30.0, live_stint=1, live_feed="A", **kw) for i in range(n)]


def t_quality_uses_the_windowed_render_skip_rate_not_the_cumulative_counter():
    # OBS can have run for hours before the broadcast, so the cumulative counter reads
    # far lower than the share of the broadcast's own frames that were skipped.
    samples = _broadcast(4, obs_render_skipped_pct=1.8, obs_render_skip_rate_pct=23.5)
    q = rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0)["quality"]
    assert (q["render_skip_rate_avg"], q["render_skip_rate_peak"]) == (23.5, 23.5), q
    assert "render_skipped_pct_peak" not in q
    html = rb.render_html(rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0))
    assert "23.5" in html and "1.8" not in html


def t_quality_render_skip_rate_is_windowed_to_on_air():
    # Off-air samples, taken before the part started, never enter the windowed figure.
    samples = [_sample(0.0, obs_render_skip_rate_pct=90.0)] + \
        [_sample(t, live_stint=1, live_feed="A", obs_render_skip_rate_pct=2.0)
         for t in (100.0, 130.0, 160.0)]
    events = [{"ts": 100.0, "type": "part_start"}, {"ts": 160.0, "type": "part_end"}]
    q = rb.build_report(samples, events, {}, "E", (0.0, 160.0), now=1000.0)["quality"]
    assert (q["render_skip_rate_avg"], q["render_skip_rate_peak"]) == (2.0, 2.0), q


def t_quality_flags_fps_below_the_configured_rate():
    q = rb.build_report(_broadcast(3, obs_fps=45.8, obs_fps_target=60.0), [], {}, "E",
                        (0.0, 60.0), now=1000.0)["quality"]
    assert (q["obs_fps_avg"], q["obs_fps_target"], q["obs_fps_low"]) == (45.8, 60.0, True), q


def t_quality_does_not_flag_fps_at_the_configured_rate():
    q = rb.build_report(_broadcast(3, obs_fps=59.9, obs_fps_target=60.0), [], {}, "E",
                        (0.0, 60.0), now=1000.0)["quality"]
    assert (q["obs_fps_target"], q["obs_fps_low"]) == (60.0, False), q


def t_quality_without_a_recorded_target_does_not_flag():
    # A DB from before v10 has no configured rate: no flag, and the page says why.
    rep = rb.build_report(_broadcast(3, obs_fps=45.8), [], {}, "E", (0.0, 60.0), now=1000.0)
    q = rep["quality"]
    assert (q["obs_fps_target"], q["obs_fps_low"]) == (None, False), q
    assert "so OBS FPS is not checked against it" in rb.render_html(rep)


def t_render_html_shows_fps_against_the_target():
    rep = rb.build_report(_broadcast(3, obs_fps=45.8, obs_fps_target=60.0), [], {}, "E",
                          (0.0, 60.0), now=1000.0)
    assert "45.8 of 60" in rb.render_html(rep)


def t_backlog_counts_only_the_on_air_feed():
    # Feed B lagging while Feed A is on air is invisible to the audience: 0 s behind.
    samples = _broadcast(4, feed_a_backlog_s=3.1, feed_b_backlog_s=25.0)
    b = rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0)["backlog"]
    assert (b["behind_s"], b["peak_s"]) == (0.0, 3.1), b


def t_backlog_behind_live_duration_and_peak():
    # Behind = floor beyond the reserve (3 s) by more than the threshold (5 s).
    samples = [_sample(0.0, live_feed="A", live_stint=1, feed_a_backlog_s=3.0),
               _sample(30.0, live_feed="A", live_stint=1, feed_a_backlog_s=12.0),
               _sample(60.0, live_feed="A", live_stint=1, feed_a_backlog_s=19.0),
               _sample(90.0, live_feed="A", live_stint=1, feed_a_backlog_s=3.2)]
    b = rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0)["backlog"]
    assert (b["behind_s"], b["peak_s"]) == (60.0, 19.0), b


def t_backlog_uses_the_given_thresholds():
    samples = _broadcast(3, feed_a_backlog_s=10.0)
    loose = rb.build_report(samples, [], {}, "E", (0.0, 60.0), now=1000.0,
                            prebuffer_s=3.0, backlog_warn_s=8.0)["backlog"]
    strict = rb.build_report(samples, [], {}, "E", (0.0, 60.0), now=1000.0,
                             prebuffer_s=3.0, backlog_warn_s=5.0)["backlog"]
    assert (loose["behind_s"], strict["behind_s"]) == (0.0, 60.0), (loose, strict)


def t_backlog_none_when_never_recorded():
    rep = rb.build_report(_broadcast(3), [], {}, "E", (0.0, 60.0), now=1000.0)
    assert rep["backlog"] is None


def t_finding_leads_with_the_backlog_then_the_frame_rate():
    samples = _broadcast(4, feed_a_backlog_s=19.0, obs_fps=45.8, obs_fps_target=60.0,
                         obs_render_skip_rate_pct=23.5)
    rep = rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0)
    f = rep["finding"]
    assert f["level"] == "red", f
    assert f["headline"] == "Output ran behind live for 1m 30s of 1m 30s on air, peak 19.0 s.", f
    assert f["cause"] == ("OBS rendered 45.8 of the configured 60 fps and skipped 23.5% of "
                          "its frames, so the output fell behind real time."), f
    html = rb.render_html(rep)
    assert f["headline"] in html and "Finding" in html
    assert html.index(f["headline"]) < html.index("On air per commentator")
    assert f"{f['headline']} {f['cause']}" in rb.render_summary_text(rep)


def t_finding_points_at_consumer_events_only_when_the_report_lists_them():
    samples = _broadcast(4, feed_a_backlog_s=19.0, obs_fps=60.0, obs_fps_target=60.0)
    bare = rb.build_report(samples, [], {}, "E", (0.0, 90.0), now=1000.0)["finding"]
    assert bare["cause"] == ("OBS held its configured frame rate (60 of 60 fps), so the "
                             "frame rate does not explain the backlog."), bare
    ev = [{"ts": 30.0, "type": "fanout_overflow", "metadata": {"feed": "A", "snaps": 2}}]
    listed = rb.build_report(samples, ev, {}, "E", (0.0, 90.0), now=1000.0)
    assert listed["finding"]["cause"].endswith("see the OBS consumer events below."), listed
    assert "OBS consumer events" in rb.render_html(listed)


def t_finding_frame_rate_only_when_no_backlog_recorded():
    rep = rb.build_report(_broadcast(3, obs_fps=45.8, obs_fps_target=60.0), [], {}, "E",
                          (0.0, 60.0), now=1000.0)
    f = rep["finding"]
    assert f["level"] == "yellow", f
    assert f["headline"] == "OBS rendered 45.8 of the configured 60 fps.", f
    assert "not recorded" in f["cause"], f


def t_finding_clean_session():
    rep = rb.build_report(_broadcast(3, feed_a_backlog_s=3.1, obs_fps=60.0,
                                     obs_fps_target=60.0), [], {}, "E", (0.0, 60.0),
                          now=1000.0)
    f = rep["finding"]
    assert f["level"] == "green", f
    assert f["headline"] == "Output stayed at the live edge (peak 3.1 s behind live).", f


def t_finding_none_without_backlog_or_fps():
    rep = rb.build_report(_broadcast(3), [], {}, "E", (0.0, 60.0), now=1000.0)
    assert rep["finding"] is None
    assert "Finding" not in rb.render_html(rep)


def t_finding_notes_the_handover_effect():
    # A backlog clears at every stint change; without one it only grows.
    one = [_sample(0.0, live_feed="A", live_stint=1, feed_a_backlog_s=3.0),
           _sample(30.0, live_feed="B", live_stint=2, feed_b_backlog_s=3.0)]
    none_ = _broadcast(2, feed_a_backlog_s=3.0)
    h1 = rb.build_report(one, [], {}, "E", (0.0, 30.0), now=1000.0)["finding"]["handover_note"]
    h0 = rb.build_report(none_, [], {}, "E", (0.0, 30.0), now=1000.0)["finding"]["handover_note"]
    assert "1 handover" in h1, h1
    assert "no handover" in h0, h0


def t_discord_payload_carries_the_finding():
    rep = rb.build_report(_broadcast(4, feed_a_backlog_s=19.0), [], {}, "E", (0.0, 90.0),
                          now=1000.0)
    f = rep["finding"]
    assert rb.report_finding_text(rep) == f"{f['headline']} {f['cause']}"
    assert rb.report_finding_text(rb.build_report(_broadcast(2), [], {}, "E", (0.0, 30.0),
                                                  now=1000.0)) == ""


def t_counter_increase_measures_the_rise_from_the_first_sample_not_its_value():
    # The first sample is the baseline: whatever the counter already held when the
    # window opened happened before it. Counting that value in full attributes the
    # relay's whole pre-event history to the event.
    assert rb.counter_increase([]) == 0
    assert rb.counter_increase([7]) == 0          # one reading shows no rise at all
    assert rb.counter_increase([0, 0, 0]) == 0
    assert rb.counter_increase([0, 1, 3, 3]) == 3
    assert rb.counter_increase([4, 6]) == 2       # not 6
    # A restart mid-window: rise to 5, counter starts over, then 2 more.
    assert rb.counter_increase([1, 5, 0, 2]) == 6


def t_a_multi_part_event_counts_each_repair_once():
    # Summing counter_increase per on-air window re-counts a running counter in full
    # at every part: a three-part event whose counter went 5 -> 9 reports 21, not 4.
    samples = []
    for ts, total in ((0.0, 5), (10.0, 5),          # Part 1
                      (100.0, 5), (110.0, 7),       # Part 2
                      (200.0, 7), (210.0, 9)):      # Part 3
        samples.append(_sample(ts, live_stint=1, av_repairs_total=total,
                               av_unexplained_total=0))
    events = [{"ts": 0.0, "type": "part_start"}, {"ts": 10.0, "type": "part_end"},
              {"ts": 100.0, "type": "part_start"}, {"ts": 110.0, "type": "part_end"},
              {"ts": 200.0, "type": "part_start"}, {"ts": 210.0, "type": "part_end"}]
    rep = rb.build_report(samples, events, {1: "Alice"}, "E", (0.0, 210.0), now=300.0)
    assert rep["on_air"]["av_repairs"] == 4, rep["on_air"]


def t_the_report_counts_the_relays_mic_remutes():
    # #721: each remute is a stretch where the producer's voice went out twice. The
    # report names how often it happened, so a producer who unmuted the OBS mic by hand
    # learns it afterwards even if nobody in chat said so.
    samples = [_sample(0.0, live_stint=1), _sample(30.0, live_stint=1)]
    events = [{"ts": 5.0, "type": "mic_remuted", "metadata": {"input": "Commentary Mic Device"}},
              {"ts": 20.0, "type": "mic_remuted", "metadata": {"input": "Commentary Mic Device"}}]
    rep = rb.build_report(samples, events, {1: "Alice"}, "E", (0.0, 30.0), now=100.0)
    assert rep["mic_remutes"] == 2, rep.get("mic_remutes")
    html = rb.render_html(rep)
    assert "muted the OBS commentary mic 2 time(s)" in html, html
    quiet = rb.build_report(samples, [], {1: "Alice"}, "E", (0.0, 30.0), now=100.0)
    assert quiet["mic_remutes"] == 0 and "commentary mic" not in rb.render_html(quiet)


def t_a_repair_between_two_parts_is_still_part_of_the_event():
    # One series across every window, not a sum per window. Per window, each part's
    # first sample is its own baseline, so a rise in the off-air gap between two parts
    # vanishes even though the relay was up and it happened during the event.
    samples = [_sample(0.0, live_stint=1, av_repairs_total=0, av_unexplained_total=0),
               _sample(10.0, live_stint=1, av_repairs_total=0, av_unexplained_total=0),
               # nothing on air between 10 and 100; the counter rises to 3 meanwhile
               _sample(100.0, live_stint=1, av_repairs_total=3, av_unexplained_total=0),
               _sample(110.0, live_stint=1, av_repairs_total=3, av_unexplained_total=0)]
    events = [{"ts": 0.0, "type": "part_start"}, {"ts": 10.0, "type": "part_end"},
              {"ts": 100.0, "type": "part_start"}, {"ts": 110.0, "type": "part_end"}]
    rep = rb.build_report(samples, events, {1: "Alice"}, "E", (0.0, 110.0), now=200.0)
    assert rep["on_air"]["av_repairs"] == 3, rep["on_air"]


def t_windows_out_of_order_do_not_read_as_a_counter_reset():
    # counter_increase reads a fall as a restart and counts the new value in full, so
    # the series it gets must be chronological. The windows come from the event list in
    # whatever order that list has, and nothing upstream promises it is sorted.
    samples = []
    for ts, total in ((0.0, 10), (10.0, 12), (100.0, 12), (110.0, 20)):
        samples.append(_sample(ts, live_stint=1, av_repairs_total=total,
                               av_unexplained_total=0))
    events = [{"ts": 100.0, "type": "part_start"}, {"ts": 110.0, "type": "part_end"},
              {"ts": 0.0, "type": "part_start"}, {"ts": 10.0, "type": "part_end"}]
    rep = rb.build_report(samples, events, {1: "Alice"}, "E", (0.0, 110.0), now=200.0)
    assert rep["on_air"]["av_repairs"] == 10, (
        "the counter rose 10 -> 20; reading the later window first makes its 20 -> 10 "
        "look like a relay restart and counts the whole counter a second time")


def t_counter_increase_skips_missing_samples_instead_of_reading_them_as_zero():
    # A database written before v11 has NULL here, and a missed tick has nothing.
    # Reading either as 0 would invent a reset and double the total. The first actual
    # reading is the baseline, so [None, 2, None, 5] is a rise of 3, not 5: whether
    # those first 2 happened inside the window is unknowable, and over-counting is the
    # worse error in a line a producer reads after the event.
    assert rb.counter_increase([None, 2, None, 5]) == 3
    assert rb.counter_increase([None, None]) == 0


def t_report_counts_av_repairs_and_the_rendered_line_says_what_it_means():
    # OBS repaired each of these itself, so the line is a record, not an alarm. It must
    # say so, or a producer reading the report goes looking for a fault. (#619)
    samples = [_sample(0.0, live_stint=1, av_repairs_total=0, av_unexplained_total=0),
               _sample(30.0, live_stint=1, av_repairs_total=2, av_unexplained_total=0),
               _sample(60.0, live_stint=1, av_repairs_total=3, av_unexplained_total=1)]
    rep = rb.build_report(samples, [], {1: "Alice"}, "E", (0.0, 60.0), now=1000.0)
    assert rep["on_air"]["av_repairs"] == 3, rep["on_air"]
    assert rep["on_air"]["av_unexplained"] == 1, rep["on_air"]
    html = rb.render_html(rep)
    assert "3 time(s)" in html
    assert "1 of them with no feed restart to explain it" in html
    assert "back in sync afterwards" in html

    # All explained: the line still appears but names no open question.
    ok = [_sample(0.0, live_stint=1, av_repairs_total=0, av_unexplained_total=0),
          _sample(30.0, live_stint=1, av_repairs_total=2, av_unexplained_total=0)]
    html2 = rb.render_html(rb.build_report(ok, [], {1: "Alice"}, "E", (0.0, 30.0),
                                           now=1000.0))
    assert "every one right after a feed restart" in html2

    # A clean event says nothing at all about A/V sync.
    clean = [_sample(0.0, live_stint=1), _sample(30.0, live_stint=1)]
    html3 = rb.render_html(rb.build_report(clean, [], {1: "Alice"}, "E", (0.0, 30.0),
                                           now=1000.0))
    assert "re-synced" not in html3


if __name__ == "__main__":
    run()

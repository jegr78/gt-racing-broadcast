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
    # a previous event (100-130) then this one (400-430) — a 270s gap < 1800s would
    # normally merge them into one window. A floor at this event's start clamps it.
    ts = [100.0, 130.0, 400.0, 430.0]
    assert rb.select_session(ts, gap_s=1800, floor=400.0) == (400.0, 430.0)
    # floor between samples keeps only those at/after it
    assert rb.select_session(ts, gap_s=1800, floor=350.0) == (400.0, 430.0)


def t_select_session_floor_none_is_unchanged():
    ts = [100.0, 130.0, 400.0, 430.0]
    assert rb.select_session(ts, gap_s=1800, floor=None) == (100.0, 430.0)


def t_select_session_floor_after_all_samples_empty():
    ts = [100.0, 130.0]
    assert rb.select_session(ts, gap_s=1800, floor=999.0) == (None, None)


def t_quality_includes_host_metrics():
    # #536: host CPU/RAM/network (already sampled) surface in the report quality section.
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
    # OBS-only samples (no sys_*) still build; host cells fall back to "—".
    samples = [_sample(0.0, obs_cpu_pct=5.0), _sample(30.0, obs_cpu_pct=6.0)]
    q = rb._quality(samples)
    assert q["sys_cpu_avg"] is None and q["net_down_avg"] is None, q


def t_quality_includes_inbound_gap_peak():
    # #535: the worst inbound inter-arrival gap across both feeds surfaces as a peak row.
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
    # a log with no parseable 'YYYY-MM-DD HH:MM:SS' prefix (e.g. OBS time-only) is
    # returned whole rather than emptied
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
    # Display-stint samples: stint 1 then stint 2, the SAME commentator across a
    # same-URL back-to-back -> credited as TWO stints for that commentator, full
    # duration preserved (#500 Problem 1).
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
    # self-contained: no external references (dotless marker avoids the CodeQL
    # incomplete-url-substring rule that has bitten test asserts before)
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
    # all within one session. The hole must NOT count as green -> uptime well below 100%.
    samples = [_sample(0.0), _sample(30.0),
               _sample(700.0), _sample(730.0)]
    rep = rb.build_report(samples, [], {}, "E", (0.0, 730.0), now=1000.0)
    assert rep["header"]["uptime_pct"] < 100.0, rep["header"]
    # green wall-clock is the two contiguous 30s intervals (~60s of 730s), NOT the whole span
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
    # #582: ring laps under OBS, automatic OBS rebuilds and the guard's stand-down are
    # facts the report must show; 26 rebuilds on 2026-08-28 were invisible afterwards.
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
    # A self-healed feed drop (auto-recovery) must show in its own report section — the
    # 2026-07-10 gap where a ~10 s stutter left the report "all green".
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
    # yield identical health_bands (no accumulating dict mutation).
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
    # off-air red BEFORE the part window must not count; in-window green = 100% uptime
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
    # A stop/restart splits the session into TWO on-air windows with an off-air gap
    # (< GAP_S) between them (exactly the N24 false-start: OBS started, stopped 47s
    # later, restarted). Health is green throughout on-air. The off-air gap must NOT
    # be bridged/counted as green -> uptime stays <= 100% (regression for the 100.3%
    # seen in the N24 report; the band that spanned the gap over-counted vs on_air_s).
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
    # commentator on-air likewise must not exceed the on-air total (was 160s > 120s)
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
    # A desync_active band contributes its (gap-filled) duration; a clean event -> 0;
    # old samples without the key -> 0 (NULL-tolerant).
    samples = [_sample(0.0, live_stint=1, desync_active=1),
               _sample(30.0, live_stint=1, desync_active=1),
               _sample(60.0, live_stint=1, desync_active=0)]
    rep = rb.build_report(samples, [], {1: "Alice"}, "E", (0.0, 60.0), now=1000.0)
    # gap-filled active band [0,60] -> exactly 60.0s (30->60 gap < GAP_S is bridged).
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
    # #523: the relay stores the real part label — a qualifying part is
    # label="Q started"/"Q ended" (metadata.index is just the pointer position, 1).
    # broadcast_timeline must show the LABEL, not "Part 1", so qualifying reports
    # don't mislabel the Q part.
    events = [{"ts": 100, "type": "part_start", "label": "Q started",
               "metadata": {"index": 1}},
              {"ts": 160, "type": "part_end", "label": "Q ended",
               "metadata": {"index": 1}}]
    tl = rb.broadcast_timeline(events)
    assert [(r["ts"], r["label"]) for r in tl] == [(100, "Q started"), (160, "Q ended")]
    # A labelless part event still falls back to "Part {index}".
    bare = [{"ts": 50, "type": "part_start", "metadata": {"index": 2}}]
    assert rb.broadcast_timeline(bare) == [{"ts": 50, "label": "Part 2 started"}]


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")


if __name__ == "__main__":
    run()

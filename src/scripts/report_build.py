#!/usr/bin/env python3
"""Pure aggregation + rendering for the post-event report (stdlib only, no I/O).

Reads the Health-DB sample/event dicts (as returned by health_store.query_range /
query_events), aggregates a session into a structured report dict, and renders a
SELF-CONTAINED HTML document (all CSS inline, system fonts, inline SVG — no external
references, so it opens identically anywhere, offline). Redaction by construction:
no stream URLs ever enter the report (name resolution passes in a stint->name map).
"""
import html as _html
import re
import time

import health_store as hs

# Coarse session gap: separates DISTINCT events in a long-retention DB. Much larger
# than health_store.GAP_S (95s, band continuity) so a producer handover — where B's
# relay is already running and mirroring — never splits one event into two reports.
SESSION_GAP_S = 1800


def select_session(sample_ts, gap_s=SESSION_GAP_S, floor=None):
    """The last contiguous run of ascending sample timestamps. A gap larger than
    gap_s ends a session. Returns (start, end) or (None, None) for no data.

    `floor` (the current event's start time, when known) discards any samples before
    it FIRST, so a quick event restart within gap_s no longer merges the previous
    event's samples into this one's window. Without a floor (older data, or a relay
    started outside the event lifecycle) the gap heuristic alone applies."""
    ts = sorted(t for t in sample_ts
                if t is not None and (floor is None or t >= floor))
    if not ts:
        return (None, None)
    start = ts[-1]
    for i in range(len(ts) - 1, 0, -1):
        if ts[i] - ts[i - 1] <= gap_s:
            start = ts[i - 1]
        else:
            break
    return (start, ts[-1])


def bucket_samples(samples, bucket_s=hs.SAMPLE_INTERVAL_S):
    """One sample per floor(ts/bucket_s) bucket, keeping the last by ts. For a single
    producer (~30s spacing) this is ~identity; during a handover overlap it halves the
    two-machine density. `samples` must be ascending by ts."""
    out, cur_key = [], None
    for s in samples:
        ts = s.get("ts")
        if ts is None:
            continue
        key = int(ts // bucket_s)
        if key == cur_key and out:
            out[-1] = s
        else:
            out.append(s)
            cur_key = key
    return out


def _fill_gaps(bands, gap_s=hs.GAP_S):
    """Return NEW bands where each band's end extends to the next band's start —
    but only across a normal sampling interval. A hole larger than gap_s means the
    relay was down (collapse_bands split there); that gap is NOT bridged, so a
    blackout is correctly excluded from the pre-gap state's duration. Does not
    mutate the input."""
    out = [dict(b) for b in bands]
    for i in range(len(out) - 1):
        nxt = out[i + 1]["from"]
        if nxt - out[i]["to"] <= gap_s:
            out[i]["to"] = nxt
    return out


def _uptime_pct(filled_bands, duration_s):
    if duration_s <= 0:
        return 0.0
    green = sum(b["to"] - b["from"] for b in filled_bands if b["state"] == "green")
    return round(green / duration_s * 100, 1)


def _feed_stats(sample_groups, down_field):
    # Bands are built per on-air window so a "down" band can never bridge the
    # off-air gap between two windows (a stop/restart) and over-count downtime.
    down = []
    for samples in sample_groups:
        bands = _fill_gaps(hs.collapse_bands(
            [(s["ts"], 1 if s.get(down_field) else 0) for s in samples]))
        down += [b for b in bands if b["state"] == 1]
    downtime = sum(b["to"] - b["from"] for b in down)
    longest = max((b["to"] - b["from"] for b in down), default=0.0)
    return {"drops": len(down), "downtime_s": round(downtime, 1),
            "longest_outage_s": round(longest, 1)}


def _num(samples, field):
    return [s.get(field) for s in samples if s.get(field) is not None]


def _avg(xs):
    return round(sum(xs) / len(xs), 1) if xs else None


def _peak(xs):
    return round(max(xs), 1) if xs else None


# #586: OBS counts as below its configured frame rate when the broadcast average falls
# more than 2% short of it (60 -> under 58.8). A healthy OBS holds its rate to a
# fraction of a frame, so this only catches a real shortfall.
FPS_LOW_RATIO = 0.98


def _fps_target(samples):
    """The configured OBS frame rate over the window (the most frequent recorded value),
    or None when no sample recorded one (a DB from before health-store v10)."""
    targets = _num(samples, "obs_fps_target")
    return max(set(targets), key=targets.count) if targets else None


def _quality(samples):
    kbps = _num(samples, "stream_kbps")
    dropped = _num(samples, "stream_dropped_pct")
    cong = _num(samples, "stream_congestion")
    cpu = _num(samples, "obs_cpu_pct")
    fps = _num(samples, "obs_fps")
    # #586: the per-interval render-skip rate, averaged over the on-air samples. NOT
    # obs_render_skipped_pct: that counter runs from OBS start, so hours of idle OBS
    # before a broadcast dilute it (1.8% shown for a 23.5% broadcast on 2026-08-28).
    # The DB keeps no raw frame counts, so a mean of interval rates is the windowed
    # figure. Every other metric here is instantaneous or resets with the output.
    rskip = _num(samples, "obs_render_skip_rate_pct")
    # #536: host machine metrics (already sampled into health-history.db). Network is
    # stored as kbps -> shown as Mbps to match btop / readability.
    sys_cpu = _num(samples, "sys_cpu_pct")
    sys_mem = _num(samples, "sys_mem_pct")
    net_down = [v / 1000.0 for v in _num(samples, "sys_net_down_kbps")]
    net_up = [v / 1000.0 for v in _num(samples, "sys_net_up_kbps")]
    # #535: worst inbound inter-arrival gap across both feeds (peak only — averaging a
    # per-interval max is meaningless). Surfaces source jitter the drop/OBS detectors miss.
    gaps = _num(samples, "feed_a_max_gap_s") + _num(samples, "feed_b_max_gap_s")
    if not any([kbps, dropped, cong, cpu, fps, rskip, sys_cpu, sys_mem, net_down, net_up, gaps]):
        return None
    fps_avg, target = _avg(fps), _fps_target(samples)
    fps_low = fps_avg is not None and target is not None and fps_avg < target * FPS_LOW_RATIO
    return {"stream_kbps_avg": _avg(kbps), "stream_kbps_peak": _peak(kbps),
            "dropped_pct_avg": _avg(dropped), "dropped_pct_peak": _peak(dropped),
            "congestion_avg": _avg(cong),
            "obs_cpu_avg": _avg(cpu), "obs_cpu_peak": _peak(cpu),
            "obs_fps_avg": fps_avg, "obs_fps_target": target, "obs_fps_low": fps_low,
            "render_skip_rate_avg": _avg(rskip), "render_skip_rate_peak": _peak(rskip),
            "sys_cpu_avg": _avg(sys_cpu), "sys_cpu_peak": _peak(sys_cpu),
            "sys_mem_avg": _avg(sys_mem), "sys_mem_peak": _peak(sys_mem),
            "net_down_avg": _avg(net_down), "net_down_peak": _peak(net_down),
            "net_up_avg": _avg(net_up), "net_up_peak": _peak(net_up),
            "inbound_gap_peak": _peak(gaps)}


def _on_air_backlog(sample):
    """The consumer backlog (s) of the feed that was on air in this sample, or None.
    Only the on-air feed's backlog reaches the audience; the off-air feed is not
    watched by anyone, whatever its ring holds."""
    live = (sample.get("live_feed") or "").upper()
    if live not in ("A", "B"):
        return None
    return sample.get(f"feed_{live.lower()}_backlog_s")


def _backlog(sample_groups, prebuffer_s, warn_s):
    """#586: how long the on-air output ran behind live, and the worst it got.

    "Behind live" is the relay's own rule (health_store.feed_backlog_degraded), so the
    report counts exactly the time the director saw the yellow reason. Durations are
    built per on-air window like the other bands. The stored value is the smallest
    backlog in each heartbeat interval, so the peak is a lower bound. None when no
    sample recorded a backlog (fan-out off, or a relay from before #583)."""
    floors = []
    behind_s = 0.0
    for samples in sample_groups:
        pts = []
        for s in samples:
            fl = _on_air_backlog(s)
            if fl is not None:
                floors.append(fl)
            pts.append((s["ts"], 1 if hs.feed_backlog_degraded(fl, prebuffer_s, warn_s)
                        else 0))
        bands = _fill_gaps(hs.collapse_bands(pts))
        behind_s += sum(b["to"] - b["from"] for b in bands if b["state"])
    if not floors:
        return None
    return {"behind_s": round(behind_s, 1), "peak_s": _peak(floors),
            "prebuffer_s": prebuffer_s, "warn_s": warn_s}


def _fmt_fps(v):
    """60.0 -> '60', 59.94 -> '59.94', 45.8 -> '45.8'."""
    return f"{v:g}"


def _finding(backlog, quality, on_air_s, handovers):
    """#586: the report's verdict, backlog first. The backlog is what the audience and
    the commentators lived through; the frame rate is why. None when neither was
    recorded. `level` is green/yellow/red for the page's colour only."""
    q = quality or {}
    fps, target, low = q.get("obs_fps_avg"), q.get("obs_fps_target"), q.get("obs_fps_low")
    rate = q.get("render_skip_rate_avg")
    if backlog is None and fps is None:
        return None
    if low:
        fps_line = f"OBS rendered {_fmt_fps(fps)} of the configured {_fmt_fps(target)} fps"
    elif fps is not None and target is not None:
        fps_line = (f"OBS held its configured frame rate ({_fmt_fps(fps)} of "
                    f"{_fmt_fps(target)} fps)")
    elif fps is not None:
        fps_line = (f"OBS rendered {_fmt_fps(fps)} fps (its configured frame rate was "
                    f"not recorded)")
    else:
        fps_line = "The OBS frame rate was not recorded"
    if backlog is not None and backlog["behind_s"] > 0:
        level = "red"
        headline = (f"Output ran behind live for {_fmt_dur(backlog['behind_s'])} of "
                    f"{_fmt_dur(on_air_s)} on air, peak {backlog['peak_s']:.1f} s.")
        if low:
            skipped = f" and skipped {rate}% of its frames" if rate else ""
            cause = f"{fps_line}{skipped}, so the output fell behind real time."
        else:
            cause = f"{fps_line}; see the OBS consumer events below."
    elif backlog is not None:
        level = "yellow" if low else "green"
        headline = (f"Output stayed at the live edge (peak {backlog['peak_s']:.1f} s "
                    f"behind live).")
        cause = f"{fps_line}."
    else:
        level = "yellow" if low else "green"
        headline = f"{fps_line}."
        cause = ("How far the output ran behind live was not recorded (feed fan-out "
                 "off, or a relay from before it was measured), so its effect on the "
                 "audience is unknown.")
    if handovers:
        handover_note = (f"A backlog clears at every stint handover; this session had "
                         f"{handovers} handover{'s' if handovers != 1 else ''}.")
    else:
        handover_note = ("A backlog clears at every stint handover; this session had no "
                         "handover, so a backlog could only grow.")
    return {"level": level, "headline": headline, "cause": cause,
            "handover_note": handover_note}


def _on_air(sample_groups, name_for_stint):
    # Per-window bands (never bridged across an off-air stop/restart gap), so a
    # commentator's on-air seconds can never exceed the on-air window total.
    agg = {}          # name -> [seconds, set(stints)]
    resolved = bool(name_for_stint)
    non_null = []
    desync_seconds = 0.0
    for samples in sample_groups:
        bands = _fill_gaps(hs.collapse_bands(
            [(s["ts"], s.get("live_stint")) for s in samples]))
        for b in bands:
            st = b["state"]
            if st is None:
                continue
            st = int(st)
            name = name_for_stint.get(st) or f"Stint {st}"
            entry = agg.setdefault(name, [0.0, set()])
            entry[0] += b["to"] - b["from"]
            entry[1].add(st)
        non_null += [int(b["state"]) for b in bands if b["state"] is not None]
        # #500: total time a ping-pong desync (#494) was active within this window —
        # the report flags it as an "attribution may be unreliable" caveat. NULL/missing
        # desync_active (old DBs) collapses to a non-active band -> contributes 0.
        dbands = _fill_gaps(hs.collapse_bands(
            [(s["ts"], 1 if s.get("desync_active") else 0) for s in samples]))
        desync_seconds += sum(b["to"] - b["from"] for b in dbands if b["state"])
    handovers = sum(1 for i in range(1, len(non_null)) if non_null[i] != non_null[i - 1])
    commentators = sorted(
        ({"name": n, "seconds": round(v[0], 1), "stints": len(v[1])} for n, v in agg.items()),
        key=lambda c: -c["seconds"])
    return {"commentators": commentators, "stint_handovers": handovers,
            "resolved": resolved, "desync_seconds": round(desync_seconds, 1)}


def _pair_windows(events, start_type, end_type, session_end):
    wins, open_s = [], None
    for e in sorted(events, key=lambda x: x.get("ts") or 0):
        t, ts = e.get("type"), e.get("ts")
        if ts is None:
            continue
        if t == start_type and open_s is None:
            open_s = ts
        elif t == end_type and open_s is not None:
            wins.append((open_s, ts))
            open_s = None
    if open_s is not None and session_end is not None:
        wins.append((open_s, session_end))
    return wins


def on_air_windows(events, session_end):
    """Intended on-air windows: part_start->part_end pairs (preferred), else
    obs_stream_start->obs_stream_stop pairs, else None (legacy whole-session)."""
    for start_t, end_t in (("part_start", "part_end"),
                           ("obs_stream_start", "obs_stream_stop")):
        wins = _pair_windows(events, start_t, end_t, session_end)
        if wins:
            return wins
    return None


def windows_total_s(windows):
    return sum(max(0.0, e - s) for s, e in windows)


def _in_windows(ts, windows):
    return any(s <= ts <= e for s, e in windows)


def _clip_samples(samples, windows):
    return [s for s in samples
            if s.get("ts") is not None and _in_windows(s["ts"], windows)]


def _sample_groups(samples, windows):
    """Partition samples into one list PER on-air window (off-air excluded). Band
    aggregations run per group so collapse_bands/_fill_gaps can never bridge the
    off-air gap between two windows (a stop/restart) and over-count on-air time.
    With no windows (legacy whole-session), one group holds every sample."""
    if not windows:
        return [list(samples)]
    return [[s for s in samples
             if s.get("ts") is not None and lo <= s["ts"] <= hi]
            for (lo, hi) in windows]


# Consumer-side events on the OBS read path (#582): the ring lapping OBS, the automatic
# OBS-input rebuild, and its effectiveness guard standing down / re-arming.
_OBS_CONSUMER_EVENTS = {
    "fanout_overflow": lambda md: f"Ring overflow under OBS ({md.get('snaps') or 1}x)",
    "obs_rebuild": lambda md: ("Automatic OBS rebuild (stall fraction "
                               f"{md.get('stall_fraction')})"),
    "obs_rebuild_stood_down": lambda md: ("Auto-rebuild stood down after "
                                          f"{md.get('attempts')} ineffective rebuilds"),
    "obs_rebuild_rearmed": lambda md: f"Auto-rebuild re-armed ({md.get('reason') or ''})",
}


def broadcast_timeline(events):
    """Chronological part (preferred) or OBS-stream start/stop rows for the report's
    Broadcast-timeline section — the reference points for the OBS-downtime figures."""
    have_parts = any(e.get("type") in ("part_start", "part_end") for e in events)
    starts = ("part_start", "part_end") if have_parts else ("obs_stream_start", "obs_stream_stop")
    rows = []
    for e in sorted(events, key=lambda x: x.get("ts") or 0):
        t = e.get("type")
        if t not in starts:
            continue
        if have_parts:
            # The relay records the real part label (e.g. a qualifying part is
            # "Q started"/"Q ended"); metadata.index is only the pointer position.
            # Prefer the stored label so the timeline shows "Q", not "Part 1".
            stored = (e.get("label") or "").strip()
            idx = (e.get("metadata") or {}).get("index")
            label = stored or (f"Part {idx} {'started' if t == 'part_start' else 'ended'}"
                               if idx is not None
                               else ("Part started" if t == "part_start" else "Part ended"))
        else:
            label = "OBS stream started" if t.endswith("start") else "OBS stream stopped"
        rows.append({"ts": e.get("ts"), "label": label})
    return rows


def build_report(samples, events, name_for_stint, event_title, window, now,
                 host=None, prebuffer_s=hs.DEFAULT_FEED_PREBUFFER_S,
                 backlog_warn_s=hs.FEED_BACKLOG_WARN_S):
    """Aggregate ONE session (already bucket-deduplicated) into the report dict.
    `window` = (from_ts, to_ts). `samples` is assumed non-empty (the caller guards).
    `host` is the producer machine's name, surfaced in the report so it is clear
    which box produced it. `prebuffer_s`/`backlog_warn_s` are the relay's fan-out
    reserve and backlog threshold, which decide when the output counts as behind live."""
    frm, to = window
    duration_s = max(0.0, (to or 0) - (frm or 0))
    windows = on_air_windows(events, to)
    groups = _sample_groups(samples, windows)
    metric_samples = [s for g in groups for s in g]
    on_air_s = windows_total_s(windows) if windows else duration_s
    # Full-session health bands drive the SVG strip (off-air visible as context);
    # the metric bands (on-air only, built PER WINDOW) drive Uptime — a band that
    # spanned the off-air gap between two windows used to push uptime over 100%.
    health_bands = _fill_gaps(hs.collapse_bands(
        [(s["ts"], s.get("health_level")) for s in samples]))
    metric_bands = [b for g in groups
                    for b in _fill_gaps(hs.collapse_bands(
                        [(s["ts"], s.get("health_level")) for s in g]))]
    on_air = _on_air(groups, name_for_stint)
    on_air["resolved_at"] = now
    feeds = [{"feed": "A", **_feed_stats(groups, "feed_a_down")},
             {"feed": "B", **_feed_stats(groups, "feed_b_down")}]
    handovers = []
    substitutions = []
    recoveries = []
    obs_consumer = []
    for e in events:
        if e.get("type") == "takeover":
            md = e.get("metadata") or {}
            handovers.append({"ts": e.get("ts"), "from": md.get("from") or "",
                              "to": e.get("producer") or "", "stint": md.get("stint")})
        elif e.get("type") == "feed_substitution":
            md = e.get("metadata") or {}
            substitutions.append({"ts": e.get("ts"), "feed": md.get("feed") or "",
                                  "stint": md.get("stint"),
                                  "streamer": name_for_stint.get(md.get("stint")) or "",
                                  "reason": md.get("reason") or ""})
        elif e.get("type") == "feed_recovery":
            md = e.get("metadata") or {}
            recoveries.append({"ts": e.get("ts"), "feed": md.get("feed") or "",
                               "stint": md.get("stint"),
                               "streamer": name_for_stint.get(md.get("stint")) or "",
                               "downtime_s": md.get("downtime_s") or 0})
        elif e.get("type") in _OBS_CONSUMER_EVENTS:
            md = e.get("metadata") or {}
            obs_consumer.append({"ts": e.get("ts"), "feed": md.get("feed") or "",
                                 "stint": md.get("stint"),
                                 "streamer": name_for_stint.get(md.get("stint")) or "",
                                 "what": _OBS_CONSUMER_EVENTS[e["type"]](md)})
    quality = _quality(metric_samples)
    backlog = _backlog(groups, prebuffer_s, backlog_warn_s)
    return {
        "header": {"event_title": event_title or "", "start": frm, "end": to,
                   "duration_s": round(duration_s, 1),
                   "on_air_s": round(on_air_s, 1),
                   "host": host or "",
                   "uptime_pct": _uptime_pct(metric_bands, on_air_s)},
        "on_air": on_air,
        "feeds": feeds,
        "incidents": [inc for g in groups for inc in hs.derive_incidents(g)],
        "quality": quality,
        "backlog": backlog,
        "finding": _finding(backlog, quality, on_air_s, on_air["stint_handovers"]),
        "producer_handovers": handovers,
        "substitutions": substitutions,
        "recoveries": recoveries,
        "obs_consumer": obs_consumer,
        "overlap_approximate": bool(handovers),
        "broadcast_timeline": broadcast_timeline(events),
        "health_bands": health_bands,
    }


# ---- rendering ----

_HEALTH_COLORS = {"green": "#2e7d32", "yellow": "#f9a825", "red": "#c62828"}


def _fmt_dur(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _fmt_clock(ts):
    if ts is None:
        return "—"
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _fmt_date(ts):
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _esc(x):
    return _html.escape("" if x is None else str(x))


def _svg_health_strip(bands, frm, to):
    """A thin inline-SVG band strip (no JS, no external refs) colouring the session
    by aggregate health. Returns '' when there is nothing to draw."""
    span = (to or 0) - (frm or 0)
    if span <= 0 or not bands:
        return ""
    w, h = 720, 20
    rects = []
    for b in bands:
        x = (b["from"] - frm) / span * w
        bw = max(0.5, (b["to"] - b["from"]) / span * w)
        color = _HEALTH_COLORS.get(b["state"], "#9e9e9e")
        rects.append(f'<rect x="{x:.1f}" y="0" width="{bw:.1f}" height="{h}" fill="{color}"/>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
            f'preserveAspectRatio="none" role="img" aria-label="Health timeline">'
            + "".join(rects) + "</svg>")


def _table(headers, rows):
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


_STYLE = """
:root{color-scheme:light}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
 margin:0;padding:32px;background:#f5f6f8;color:#1c1e21;line-height:1.45}
.wrap{max-width:820px;margin:0 auto;background:#fff;border-radius:12px;padding:32px;
 box-shadow:0 1px 3px rgba(0,0,0,.12)}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px;
 border-bottom:2px solid #eceef1;padding-bottom:6px}
.sub{color:#65676b;font-size:13px;margin:0 0 20px}
.kpis{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0}
.kpi{background:#f5f6f8;border-radius:8px;padding:12px 16px;min-width:120px}
.kpi .n{font-size:22px;font-weight:700}.kpi .l{font-size:12px;color:#65676b}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #eceef1}
th{color:#65676b;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.caveat{font-size:12px;color:#65676b;font-style:italic;margin-top:8px}
.note{font-size:12px;color:#65676b;margin-top:6px}
.sev-red{color:#c62828;font-weight:700}.sev-yellow{color:#b26a00;font-weight:600}
.finding{border-left:4px solid #9e9e9e;background:#f5f6f8;border-radius:8px;
 padding:12px 16px;margin:16px 0}
.finding.lvl-green{border-color:#2e7d32}.finding.lvl-yellow{border-color:#f9a825}
.finding.lvl-red{border-color:#c62828}
.finding .head{font-size:16px;font-weight:700;margin:0 0 4px}
.finding p{margin:4px 0;font-size:14px}.finding p.note{font-size:12px}
"""


def _fps_cell(q):
    """'45.8 of 60 ⚠' against the configured rate; the bare average when none is known."""
    avg, target = q.get("obs_fps_avg"), q.get("obs_fps_target")
    if avg is None or target is None:
        return avg
    return f"{_fmt_fps(avg)} of {_fmt_fps(target)}" + (" \u26a0 below" if q.get("obs_fps_low")
                                                        else "")


def render_html(report):
    """A single self-contained HTML document: inline <style>, inline SVG, no external
    references. Safe to attach to Discord / open offline anywhere."""
    hd = report["header"]
    oa = report["on_air"]
    parts = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             f"<title>Post-event report — {_esc(hd['event_title'] or 'Event')}</title>",
             f"<style>{_STYLE}</style></head><body><div class='wrap'>"]
    parts.append(f"<h1>{_esc(hd['event_title'] or 'Post-event report')}</h1>")
    host_sub = f" · {_esc(hd['host'])}" if hd.get("host") else ""
    parts.append(f"<p class='sub'>{_esc(_fmt_date(hd['start']))} · "
                 f"{_esc(_fmt_clock(hd['start']))}–{_esc(_fmt_clock(hd['end']))} · "
                 f"{_esc(_fmt_dur(hd['duration_s']))}{host_sub}</p>")
    parts.append("<div class='kpis'>"
                 f"<div class='kpi'><div class='n'>{hd['uptime_pct']}%</div>"
                 "<div class='l'>Uptime</div></div>"
                 f"<div class='kpi'><div class='n'>{_esc(_fmt_dur(hd['on_air_s']))}</div>"
                 "<div class='l'>On air</div></div>"
                 f"<div class='kpi'><div class='n'>{_esc(_fmt_dur(hd['duration_s']))}</div>"
                 "<div class='l'>Session length</div></div>"
                 f"<div class='kpi'><div class='n'>{len(report['incidents'])}</div>"
                 "<div class='l'>Incidents</div></div></div>")
    strip = _svg_health_strip(report["health_bands"], hd["start"], hd["end"])
    if strip:
        parts.append(strip)

    # Finding (#586): the verdict first, backlog before frame rate.
    fd = report.get("finding")
    if fd:
        parts.append(f"<div class='finding lvl-{_esc(fd['level'])}'>"
                     "<h2 style='border:0;margin:0 0 6px;padding:0'>Finding</h2>"
                     f"<p class='head'>{_esc(fd['headline'])}</p>"
                     f"<p>{_esc(fd['cause'])}</p>"
                     f"<p class='note'>{_esc(fd['handover_note'])}</p>")
        if report.get("backlog"):
            bl = report["backlog"]
            parts.append("<p class='note'>Behind live means more than "
                         f"{_esc(_fmt_fps(bl['warn_s']))} s beyond the "
                         f"{_esc(_fmt_fps(bl['prebuffer_s']))} s fan-out reserve, on the "
                         "feed that was on air. Each value is the smallest backlog in its "
                         "30 s interval, so the peak is a lower bound.</p>")
        parts.append("</div>")

    # On air per commentator
    parts.append("<h2>On air per commentator</h2>")
    rows = [(c["name"], _fmt_dur(c["seconds"]), c["stints"]) for c in oa["commentators"]]
    parts.append(_table(["Commentator", "On air", "Stints"], rows) if rows
                 else "<p class='note'>No on-air data recorded.</p>")
    if oa["resolved"]:
        parts.append(f"<p class='caveat'>Names resolved from the schedule as of "
                     f"{_esc(_fmt_clock(oa['resolved_at']))}; a later running-order edit "
                     f"could change them.</p>")
    else:
        parts.append("<p class='caveat'>Commentator names were unavailable (relay not "
                     "running at report time) — shown by stint index.</p>")
    if oa.get("desync_seconds", 0) > 0:
        parts.append(f"<p class='caveat'>&#9888; A ping-pong desync was active for "
                     f"{_esc(_fmt_dur(oa['desync_seconds']))} of this event — "
                     f"per-commentator attribution during those windows may be "
                     f"unreliable.</p>")

    # Producer handovers
    if report["producer_handovers"]:
        parts.append("<h2>Producer handovers</h2>")
        hrows = [(_fmt_clock(h["ts"]), f"{h['from'] or '—'} → {h['to'] or '—'}",
                  h["stint"] if h["stint"] is not None else "—")
                 for h in report["producer_handovers"]]
        parts.append(_table(["Time", "Handover", "Stint"], hrows))

    # Stream substitutions
    if report.get("substitutions"):
        parts.append("<h2>Stream substitutions</h2>")
        parts.append("<p class='note'>Ad-hoc on-air stream swaps (the on-air feed was "
                     "pointed at an alternative source mid-stint).</p>")
        srows = [(_fmt_clock(s["ts"]), s["feed"],
                  s["stint"] if s["stint"] is not None else "—",
                  s["streamer"], s["reason"])
                 for s in report["substitutions"]]
        parts.append(_table(["Time", "Feed", "Stint", "Commentator", "Reason"], srows))

    # Feed auto-recoveries: a feed dropped and self-healed BEFORE the 30 s outage
    # threshold, so it is NOT counted in the Drops column below — but each was a brief
    # on-air glitch (the class the 2026-07-10 report showed as "all green").
    if report.get("recoveries"):
        parts.append("<h2>Feed auto-recoveries</h2>")
        parts.append("<p class='note'>Feeds that dropped and auto-recovered (stream restart / "
                     "brief timeout) — self-healed, so not counted as Drops below, but each was "
                     "a short on-air interruption. Repeated recoveries of one feed indicate an "
                     "unstable source.</p>")
        rrows = [(_fmt_clock(r["ts"]), r["feed"],
                  r["stint"] if r["stint"] is not None else "—",
                  r["streamer"], _fmt_dur(r["downtime_s"]))
                 for r in report["recoveries"]]
        parts.append(_table(["Time", "Feed", "Stint", "Commentator", "Degraded"], rrows))

    # OBS consumer events (#582): the relay could not keep OBS fed in real time, or had to
    # rebuild OBS's input. A stood-down auto-rebuild means OBS itself could not keep up.
    if report.get("obs_consumer"):
        parts.append("<h2>OBS consumer events</h2>")
        parts.append("<p class='note'>Ring overflows (OBS read slower than real time and "
                     "lost bytes), automatic OBS input rebuilds (each a short black "
                     "dropout) and the auto-rebuild stand-down after rebuilds stopped "
                     "helping.</p>")
        orows = [(_fmt_clock(o["ts"]), o["feed"],
                  o["stint"] if o["stint"] is not None else "—",
                  o["streamer"], o["what"])
                 for o in report["obs_consumer"]]
        parts.append(_table(["Time", "Feed", "Stint", "Commentator", "Event"], orows))

    # Feed reliability
    parts.append("<h2>Feed reliability</h2>")
    frows = [(f["feed"], f["drops"], _fmt_dur(f["downtime_s"]), _fmt_dur(f["longest_outage_s"]))
             for f in report["feeds"]]
    parts.append(_table(["Feed", "Drops", "Downtime", "Longest outage"], frows))
    parts.append("<p class='note'>POV (optional picture-in-picture) is not tracked for "
                 "reliability — it has no drop signal and is paused by design.</p>")

    # Broadcast timeline (part / OBS-stream start & stop — context for downtime)
    tl = report.get("broadcast_timeline") or []
    if tl:
        parts.append("<h2>Broadcast timeline</h2>")
        parts.append(_table(["Time", "Event"],
                            [(_fmt_clock(r["ts"]), r["label"]) for r in tl]))
        parts.append("<p class='note'>Uptime and the incident log below cover on-air "
                     "time only; intentional off-air (before start, between parts, "
                     "after the final stop) is excluded.</p>")

    # Incident log
    parts.append("<h2>Incident log</h2>")
    if report["incidents"]:
        irows = []
        for inc in report["incidents"]:
            sev = inc.get("severity") or ""
            irows.append((f"{_fmt_clock(inc['ts'])}–{_fmt_clock(inc['end'])}",
                          sev.upper(), _fmt_dur(inc["duration_s"]), inc.get("label") or ""))
        parts.append(_table(["Window", "Severity", "Duration", "Reason"], irows))
    else:
        parts.append("<p class='note'>No incidents — green the whole session.</p>")

    # Stream & OBS quality
    q = report["quality"]
    if q is not None:
        parts.append("<h2>Stream &amp; OBS quality</h2>")
        qrows = [("Stream bitrate (kbps)", q["stream_kbps_avg"], q["stream_kbps_peak"]),
                 ("Dropped frames (%)", q["dropped_pct_avg"], q["dropped_pct_peak"]),
                 ("Congestion", q["congestion_avg"], "—"),
                 ("OBS CPU (%)", q["obs_cpu_avg"], q["obs_cpu_peak"]),
                 ("OBS FPS", _fps_cell(q), "—"),
                 ("Render skipped (% per interval)", q["render_skip_rate_avg"],
                  q["render_skip_rate_peak"]),
                 ("Host CPU (%)", q["sys_cpu_avg"], q["sys_cpu_peak"]),
                 ("Host RAM (%)", q["sys_mem_avg"], q["sys_mem_peak"]),
                 ("Net down (Mbps)", q["net_down_avg"], q["net_down_peak"]),
                 ("Net up (Mbps)", q["net_up_avg"], q["net_up_peak"]),
                 ("Max inbound gap (s)", "—", q["inbound_gap_peak"])]
        parts.append(_table(["Metric", "Average", "Peak"],
                            [(m, a if a is not None else "—", p if p is not None else "—")
                             for m, a, p in qrows]))
        if q["obs_fps_avg"] is not None and q["obs_fps_target"] is None:
            parts.append("<p class='note'>The configured frame rate was not recorded (a "
                         "relay from before it was sampled), so OBS FPS is not checked "
                         "against it.</p>")

    if report["overlap_approximate"]:
        parts.append("<p class='caveat'>A producer handover occurred during this session; "
                     "metrics inside the overlap window are approximate (concurrent, "
                     "source-untagged samples from two machines).</p>")
    parts.append("</div></body></html>")
    return "".join(parts)


def render_summary_text(report):
    """A short plaintext headline block for CLI stdout."""
    hd = report["header"]
    lines = [f"Post-event report — {hd['event_title'] or 'Event'}",
             f"  {_fmt_date(hd['start'])} {_fmt_clock(hd['start'])}–{_fmt_clock(hd['end'])} "
             f"({_fmt_dur(hd['duration_s'])})",
             f"  Uptime {hd['uptime_pct']}% · {len(report['incidents'])} incident(s)"]
    if report.get("finding"):
        lines.append(f"  {report['finding']['headline']}")
    for f in report["feeds"]:
        lines.append(f"  Feed {f['feed']}: {f['drops']} drop(s), "
                     f"{_fmt_dur(f['downtime_s'])} down")
    return "\n".join(lines)


def report_filename(event_title, date_str):
    """<date>-<slug>.html; empty title -> <date>-report.html."""
    slug = "".join(c if c.isalnum() else "-" for c in (event_title or "").lower())
    slug = "-".join(p for p in slug.split("-") if p)
    return f"{date_str}-{slug or 'report'}.html"


def report_finding_text(report):
    """The finding's headline for the Discord embed description, or '' (#586)."""
    return (report.get("finding") or {}).get("headline") or ""


def report_discord_fields(report):
    """Pre-formatted KPI (name, value) pairs for the Discord report embed."""
    hd = report["header"]
    return [("Uptime", f"{hd['uptime_pct']}%"),
            ("On air", _fmt_dur(hd.get("on_air_s", hd["duration_s"]))),
            ("Incidents", str(len(report["incidents"]))),
            ("Session length", _fmt_dur(hd["duration_s"])),
            ("Window", f"{_fmt_clock(hd['start'])}–{_fmt_clock(hd['end'])}")]


_LOG_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def _parse_log_ts(line):
    """Epoch seconds from a leading 'YYYY-MM-DD HH:MM:SS' log prefix, else None.
    Interpreted in local time — the log formatter (logsetup) writes local time."""
    m = _LOG_TS_RE.match(line)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def slice_log_by_window(text, frm, to, margin_s=120.0):
    """Keep log lines whose leading 'YYYY-MM-DD HH:MM:SS' timestamp falls within
    [frm-margin, to+margin]. Lines without a parseable leading timestamp
    (continuations, tracebacks) are kept when the most recent timestamped line was
    in-window, so multi-line records survive intact. `frm`/`to` are epoch seconds;
    when either is None the text is returned unchanged (no window to clip to).

    A log whose format this parser doesn't recognise at all (no line carried a
    parseable timestamp — e.g. OBS's time-only 'HH:MM:SS.mmm' prefix) is returned
    WHOLE rather than emptied: better to over-include a foreign-format log than to
    silently drop it. Such files are usually per-session already (OBS writes a fresh
    log per launch), so the whole file is roughly the event window anyway."""
    if frm is None or to is None:
        return text
    lo, hi = frm - margin_s, to + margin_s
    out, keep, saw_ts = [], False, False
    for line in text.splitlines(keepends=True):
        ts = _parse_log_ts(line)
        if ts is not None:
            saw_ts, keep = True, lo <= ts <= hi
        if keep:
            out.append(line)
    return "".join(out) if saw_ts else text

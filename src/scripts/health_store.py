#!/usr/bin/env python3
"""Pure logic for the relay health-history time-series (runtime/<profile>/health-history.db).

SQLite-backed (stdlib `sqlite3`) — the only non-JSON store in the repo. No network,
no argv parsing: schema/migrations, sample insert, range query, the band/incident/
numeric-series derivations, retention pruning, and JSON-Lines export/import-merge.
Imported by the relay (HealthStore wrapper) and the `racecast health` CLI so both
agree on the on-disk shape. Redaction by construction: there is NO stream-URL or
sheet_id column, so the DB is safe to expose, export, and pull over Funnel.
"""
import json
import math
import sqlite3
import time

SCHEMA_VERSION = 10

SAMPLE_INTERVAL_S = 30          # heartbeat tick = sample cadence
LIVE_WINDOW_S = 900            # default range when no from/to given (15 min)
GAP_S = 95                     # inter-sample gap > this = relay was down (no band spans it)
DEFAULT_MAX_POINTS = 2000      # numeric-series downsample cap per metric
DEFAULT_RETENTION_DAYS = 30

# #583/#586: when a fan-out consumer counts as "behind live". Shared by the relay (the
# yellow health reason) and the post-event report (the backlog verdict), so both apply
# the one definition the director saw.
DEFAULT_FEED_PREBUFFER_S = 3.0   # #533: seconds a broadcast consumer joins behind the fan-out live edge
FEED_BACKLOG_WARN_S = 5.0        # #583: consumer backlog (s) beyond the #533 reserve that shows yellow


def feed_prebuffer_s(environ, default=DEFAULT_FEED_PREBUFFER_S):
    """Seconds OBS and the program-audio monitor join behind the fan-out live edge
    (#533). Absent/empty/non-numeric/non-finite -> default; a valid finite number
    (incl. 0) is used as-is; negatives clamp to 0.0 (disabled = today's live-edge
    join). Pure so the knob is unit-testable."""
    raw = str(environ.get("RACECAST_FEED_PREBUFFER_S", "")).strip()
    if raw == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    if not math.isfinite(v):
        return default                       # reject nan/inf -> default
    return max(0.0, v)


def feed_backlog_warn_s(environ):
    """#583 display threshold: seconds of consumer backlog beyond the fan-out reserve.
    A placeholder until #581 stage 4 calibrates it on real hardware. Absent/empty/
    non-numeric/<=0 -> default. Pure."""
    try:
        v = float(str(environ.get("RACECAST_FEED_BACKLOG_WARN_S", "")).strip())
    except (TypeError, ValueError):
        return FEED_BACKLOG_WARN_S
    return v if v > 0 else FEED_BACKLOG_WARN_S


def feed_backlog_degraded(floor_s, prebuffer_s, warn_s):
    """#583: a consumer is falling behind the live edge when its interval floor (the
    smallest backlog seen in one heartbeat interval) exceeds the #533 reserve by more
    than warn_s. The reserve itself is the healthy baseline, not a backlog. Pure."""
    return floor_s is not None and floor_s - prebuffer_s > warn_s


# Column order is the insert order. NO url/channel/sheet columns (redaction).
COLUMNS = (
    "ts", "kind",
    "health_level", "health_reasons",
    "feed_a_state", "feed_a_down", "feed_a_stint",
    "feed_b_state", "feed_b_down", "feed_b_stint",
    "pov_state", "obs_reachable",
    "source_last_ok_age_s", "source_count",
    "cookies_present", "cookies_age_h", "cookies_stale",
    "timer_mode", "timer_push",
    "mode", "live_feed", "live_stint", "desync_active",
    # v3: OBS stats + connectivity + feed quality (redaction-safe: no URLs)
    "stream_active", "stream_reconnecting", "funnel_ok", "sheet_push_ok",
    "tailscale_up", "companion_ok",
    "stream_kbps", "stream_dropped_pct", "stream_congestion",
    "obs_cpu_pct", "obs_mem_mb", "obs_fps", "obs_render_skipped_pct",
    "obs_render_skip_rate_pct",   # v7: per-interval render-skip rate (#488 drift signal)
    "obs_disk_free_mb",
    "feed_a_quality", "feed_b_quality", "pov_quality",
    # v5: machine (host) resources — distinct from the obs_* process stats above
    "sys_cpu_pct", "sys_mem_pct", "sys_net_up_kbps", "sys_net_down_kbps",
    "sys_disk_free_mb",
    "feed_a_max_gap_s", "feed_b_max_gap_s",
    # v9: fan-out consumer backlog behind the live edge, interval floor (#583)
    "feed_a_backlog_s", "feed_b_backlog_s", "pov_backlog_s",
    # v10: OBS's configured frame rate, the reference obs_fps is judged against (#586)
    "obs_fps_target",
)

BAND_FIELDS = ("health_level", "feed_a_state", "feed_b_state",
               "pov_state", "obs_reachable", "cookies_stale", "timer_push",
               "stream_active", "stream_reconnecting", "funnel_ok",
               "sheet_push_ok", "tailscale_up", "companion_ok")
NUMERIC_FIELDS = ("source_last_ok_age_s", "cookies_age_h",
                  "stream_kbps", "stream_dropped_pct", "stream_congestion",
                  "obs_cpu_pct", "obs_mem_mb", "obs_fps",
                  "obs_render_skipped_pct", "obs_render_skip_rate_pct",
                  "obs_disk_free_mb",
                  "sys_cpu_pct", "sys_mem_pct", "sys_net_up_kbps",
                  "sys_net_down_kbps", "sys_disk_free_mb",
                  "feed_a_max_gap_s", "feed_b_max_gap_s",
                  "feed_a_backlog_s", "feed_b_backlog_s", "pov_backlog_s",
                  "obs_fps_target")
STATE_KEY_FIELDS = ("health_level", "feed_a_state", "feed_a_down",
                    "feed_b_state", "feed_b_down", "pov_state", "obs_reachable",
                    "timer_push",
                    "stream_active", "stream_reconnecting", "funnel_ok",
                    "sheet_push_ok", "tailscale_up", "companion_ok")

_CREATE = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    health_level TEXT, health_reasons TEXT,
    feed_a_state TEXT, feed_a_down INTEGER, feed_a_stint INTEGER,
    feed_b_state TEXT, feed_b_down INTEGER, feed_b_stint INTEGER,
    pov_state TEXT, obs_reachable INTEGER,
    source_last_ok_age_s REAL, source_count INTEGER,
    cookies_present INTEGER, cookies_age_h REAL, cookies_stale INTEGER,
    timer_mode TEXT, timer_push TEXT,
    mode TEXT, live_feed TEXT, live_stint INTEGER, desync_active INTEGER,
    stream_active INTEGER, stream_reconnecting INTEGER,
    funnel_ok INTEGER, sheet_push_ok INTEGER,
    tailscale_up INTEGER, companion_ok INTEGER,
    stream_kbps REAL, stream_dropped_pct REAL, stream_congestion REAL,
    obs_cpu_pct REAL, obs_mem_mb REAL, obs_fps REAL,
    obs_render_skipped_pct REAL, obs_render_skip_rate_pct REAL, obs_disk_free_mb REAL,
    feed_a_quality TEXT, feed_b_quality TEXT, pov_quality TEXT,
    sys_cpu_pct REAL, sys_mem_pct REAL, sys_net_up_kbps REAL,
    sys_net_down_kbps REAL, sys_disk_free_mb REAL,
    feed_a_max_gap_s REAL, feed_b_max_gap_s REAL,
    feed_a_backlog_s REAL, feed_b_backlog_s REAL, pov_backlog_s REAL,
    obs_fps_target REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples (ts);

-- v4: discrete annotations (producer takeover, OBS stream start/stop), separate
-- from the numeric `samples` time series. Redaction-safe: no URL/sheet columns.
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    label TEXT, producer TEXT, metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
"""

EVENT_COLUMNS = ("ts", "type", "label", "producer", "metadata")

# v3 columns, added via ALTER TABLE to pre-v3 DBs (fresh DBs get them from _CREATE).
_V3_COLUMNS = (
    ("stream_active", "INTEGER"), ("stream_reconnecting", "INTEGER"),
    ("funnel_ok", "INTEGER"), ("sheet_push_ok", "INTEGER"),
    ("tailscale_up", "INTEGER"), ("companion_ok", "INTEGER"),
    ("stream_kbps", "REAL"), ("stream_dropped_pct", "REAL"),
    ("stream_congestion", "REAL"), ("obs_cpu_pct", "REAL"),
    ("obs_mem_mb", "REAL"), ("obs_fps", "REAL"),
    ("obs_render_skipped_pct", "REAL"), ("obs_disk_free_mb", "REAL"),
    ("feed_a_quality", "TEXT"), ("feed_b_quality", "TEXT"),
    ("pov_quality", "TEXT"),
)

_V5_COLUMNS = (
    ("sys_cpu_pct", "REAL"), ("sys_mem_pct", "REAL"),
    ("sys_net_up_kbps", "REAL"), ("sys_net_down_kbps", "REAL"),
    ("sys_disk_free_mb", "REAL"),
)

_V6_COLUMNS = (
    ("desync_active", "INTEGER"),
)

_V7_COLUMNS = (
    ("obs_render_skip_rate_pct", "REAL"),
)

_V8_COLUMNS = (
    ("feed_a_max_gap_s", "REAL"), ("feed_b_max_gap_s", "REAL"),   # #535 inbound stall
)

_V9_COLUMNS = (
    ("feed_a_backlog_s", "REAL"), ("feed_b_backlog_s", "REAL"),   # #583 consumer backlog
    ("pov_backlog_s", "REAL"),
)

_V10_COLUMNS = (
    ("obs_fps_target", "REAL"),   # #586 configured OBS frame rate
)


def open_db(path):
    """Open (creating the file/dirs as needed) with WAL + a busy timeout so the
    heartbeat writer and request-thread readers don't trip over each other."""
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate(conn):
    """Create the schema, add any missing v3, v5-v10 columns (lossless upgrade from v2/v3),
    and stamp user_version. Idempotent and version-agnostic."""
    conn.executescript(_CREATE)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
    for name, decl in (_V3_COLUMNS + _V5_COLUMNS + _V6_COLUMNS + _V7_COLUMNS + _V8_COLUMNS + _V9_COLUMNS
                       + _V10_COLUMNS):
        if name not in have:
            conn.execute(f"ALTER TABLE samples ADD COLUMN {name} {decl}")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()


def _row_to_dict(row):
    d = dict(row)
    raw = d.get("health_reasons")
    try:
        d["health_reasons"] = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        d["health_reasons"] = []
    return d


def record(conn, snapshot, kind, now=None):
    """Insert one sample row from a snapshot dict. Missing keys store NULL;
    health_reasons (a list) is JSON-encoded. Returns the stored row as a dict."""
    s = dict(snapshot)
    if now is not None and s.get("ts") is None:
        s["ts"] = now
    s["kind"] = kind
    s["health_reasons"] = json.dumps(s.get("health_reasons") or [])
    values = [s.get(col) for col in COLUMNS]
    placeholders = ",".join("?" for _ in COLUMNS)
    conn.execute(f"INSERT INTO samples ({','.join(COLUMNS)}) VALUES ({placeholders})", values)
    conn.commit()
    stored = {c: s.get(c) for c in COLUMNS}
    stored["health_reasons"] = snapshot.get("health_reasons") or []
    return stored


def _event_row_to_dict(row):
    d = dict(row)
    raw = d.get("metadata")
    try:
        d["metadata"] = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        d["metadata"] = None
    return d


def record_event(conn, ts, event_type, label="", producer="", metadata=None):
    """Insert one discrete annotation into the `events` table (producer takeover,
    OBS stream start/stop) — separate from the numeric `samples` series. metadata
    (a dict) is JSON-encoded; an empty/None metadata stores NULL. Returns the
    stored row as a dict (metadata parsed back)."""
    meta_json = json.dumps(metadata) if metadata else None
    conn.execute("INSERT INTO events (ts, type, label, producer, metadata) "
                 "VALUES (?,?,?,?,?)",
                 (ts, event_type, label or "", producer or "", meta_json))
    conn.commit()
    return {"ts": ts, "type": event_type, "label": label or "",
            "producer": producer or "", "metadata": metadata or None}


def query_events(conn, frm, to):
    """Events with frm <= ts <= to, ascending. metadata parsed back to a dict/None."""
    cur = conn.execute("SELECT ts, type, label, producer, metadata FROM events "
                       "WHERE ts>=? AND ts<=? ORDER BY ts ASC", (frm, to))
    return [_event_row_to_dict(r) for r in cur.fetchall()]


def annotate_latest_event(conn, event_type, patch):
    """Merge dict `patch` into the metadata of the MOST RECENT event of
    `event_type` (by ts, then rowid). Returns the updated event as a dict (metadata
    parsed back), or None when no such event exists. Used to attach a reason to the
    latest stream substitution."""
    row = conn.execute(
        "SELECT rowid, ts, type, label, producer, metadata FROM events "
        "WHERE type=? ORDER BY ts DESC, rowid DESC LIMIT 1", (event_type,)).fetchone()
    if row is None:
        return None
    rowid, ts, etype, label, producer, raw = row
    md = json.loads(raw) if raw else {}
    md.update(patch)
    conn.execute("UPDATE events SET metadata=? WHERE rowid=?", (json.dumps(md), rowid))
    conn.commit()
    return {"ts": ts, "type": etype, "label": label or "",
            "producer": producer or "", "metadata": md}


def query_range(conn, frm, to):
    """Samples with frm <= ts <= to, ascending. health_reasons parsed to a list."""
    cur = conn.execute("SELECT * FROM samples WHERE ts>=? AND ts<=? ORDER BY ts ASC",
                       (frm, to))
    return [_row_to_dict(r) for r in cur.fetchall()]


def collapse_bands(points, gap_s=GAP_S):
    """Collapse [(ts, value), ...] (ascending) into contiguous bands
    {from,to,state}. A new band starts when the value changes OR the gap to the
    previous sample exceeds gap_s (the relay was down — never bridge it)."""
    bands = []
    for ts, val in points:
        if bands and bands[-1]["state"] == val and (ts - bands[-1]["to"]) <= gap_s:
            bands[-1]["to"] = ts
        else:
            bands.append({"from": ts, "to": ts, "state": val})
    return bands


def derive_bands(samples, gap_s=GAP_S):
    """One band list per BAND_FIELD, from ordered samples."""
    return {field: collapse_bands([(s["ts"], s.get(field)) for s in samples], gap_s)
            for field in BAND_FIELDS}


def _incident_label(level, reasons):
    if reasons:
        return reasons[0]
    return {"red": "CRITICAL", "yellow": "DEGRADED"}.get(level, level or "")


def derive_incidents(samples, gap_s=GAP_S):
    """Every non-green aggregate-health band becomes an incident. Its duration runs
    until recovery: the next band's start (the recovering sample) when that gap is
    <= gap_s, else the band is extended by one SAMPLE_INTERVAL_S. This never bridges
    a relay-down hole (> gap_s) and never reports a zero-width single-sample blip."""
    reasons_at = {s["ts"]: s.get("health_reasons") or [] for s in samples}
    bands = collapse_bands([(s["ts"], s.get("health_level")) for s in samples], gap_s)
    out = []
    for i, b in enumerate(bands):
        if b["state"] == "green" or b["state"] is None:
            continue
        nxt = bands[i + 1]["from"] if i + 1 < len(bands) else None
        if nxt is not None and (nxt - b["to"]) <= gap_s:
            end = nxt
        else:
            end = b["to"] + SAMPLE_INTERVAL_S
        out.append({"ts": b["from"], "end": end, "duration_s": end - b["from"],
                    "severity": b["state"],
                    "label": _incident_label(b["state"], reasons_at.get(b["from"]))})
    return out


def downsample(pairs, max_points):
    """Bucket [(ts,val), ...] to <= max_points, taking the last point in each
    time-bucket (cheap, monotonic-x safe). The newest point is always retained."""
    n = len(pairs)
    if max_points <= 0 or n <= max_points:
        return list(pairs)
    bucket = math.ceil(n / max_points)
    out = []
    for i in range(0, n, bucket):
        out.append(pairs[min(i + bucket - 1, n - 1)])
    return out


def numeric_series(samples, max_points=DEFAULT_MAX_POINTS):
    """Per numeric field: drop None, downsample, split into parallel t/v arrays."""
    out = {}
    for field in NUMERIC_FIELDS:
        pairs = [(s["ts"], s.get(field)) for s in samples if s.get(field) is not None]
        pairs = downsample(pairs, max_points)
        out[field] = {"t": [p[0] for p in pairs], "v": [p[1] for p in pairs]}
    return out


def prune(conn, retention_days=DEFAULT_RETENTION_DAYS, now=None):
    """Delete samples older than retention_days. Returns the deleted row count."""
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400
    cur = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def export_jsonl_line(row):
    """Serialize one sample dict (health_reasons may be a list) to a JSON line."""
    r = {c: row.get(c) for c in COLUMNS}
    r["kind"] = row.get("kind")
    reasons = row.get("health_reasons")
    r["health_reasons"] = reasons if isinstance(reasons, list) else []
    return json.dumps(r, ensure_ascii=False)


def export_event_jsonl_line(row):
    """Serialize one event dict to a JSON line tagged `"_kind":"event"` so
    import_jsonl routes it to the events table (one body carries samples + events)."""
    return json.dumps({"_kind": "event", "ts": row.get("ts"), "type": row.get("type"),
                       "label": row.get("label") or "",
                       "producer": row.get("producer") or "",
                       "metadata": row.get("metadata")}, ensure_ascii=False)


def export_events_jsonl(conn, frm=0):
    """All events with ts >= frm as tagged JSON lines, ascending."""
    cur = conn.execute("SELECT ts, type, label, producer, metadata FROM events "
                       "WHERE ts>=? ORDER BY ts ASC", (frm,))
    return [export_event_jsonl_line(_event_row_to_dict(r)) for r in cur.fetchall()]


def export_jsonl(conn, frm=0):
    """All samples with ts >= frm as JSON lines (ascending), FOLLOWED BY the events
    (tagged `_kind=event`) so a single body carries both for takeover/export."""
    cur = conn.execute("SELECT * FROM samples WHERE ts>=? ORDER BY ts ASC", (frm,))
    out = [export_jsonl_line(_row_to_dict(row)) for row in cur.fetchall()]
    out.extend(export_events_jsonl(conn, frm))
    return out


def import_jsonl(conn, lines):
    """Merge JSON-Lines into the DB. Sample lines dedup by (ts, kind); event lines
    (tagged `"_kind":"event"`) route to the events table, dedup by (ts, type).
    Malformed lines are skipped (never fatal). Returns the number of rows newly
    inserted (samples + events)."""
    inserted = 0
    placeholders = ",".join("?" for _ in COLUMNS)
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("_kind") == "event":
            if obj.get("ts") is None or obj.get("type") is None:
                continue
            dup = conn.execute("SELECT 1 FROM events WHERE ts=? AND type=? LIMIT 1",
                               (obj["ts"], obj["type"])).fetchone()
            if dup:
                continue
            meta = obj.get("metadata")
            conn.execute("INSERT INTO events (ts, type, label, producer, metadata) "
                         "VALUES (?,?,?,?,?)",
                         (obj["ts"], obj["type"], obj.get("label") or "",
                          obj.get("producer") or "",
                          json.dumps(meta) if meta else None))
            inserted += 1
            continue
        if obj.get("ts") is None or obj.get("kind") is None:
            continue
        dup = conn.execute("SELECT 1 FROM samples WHERE ts=? AND kind=? LIMIT 1",
                           (obj["ts"], obj["kind"])).fetchone()
        if dup:
            continue
        s = dict(obj)
        s["health_reasons"] = json.dumps(s.get("health_reasons") or [])
        values = [s.get(col) for col in COLUMNS]
        conn.execute(f"INSERT INTO samples ({','.join(COLUMNS)}) VALUES ({placeholders})",
                     values)
        inserted += 1
    conn.commit()
    return inserted

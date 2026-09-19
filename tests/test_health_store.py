#!/usr/bin/env python3
"""Stdlib unit checks for the health-history store. Run: python3 tests/test_health_store.py"""
import importlib.util
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


hs = _load("health_store", ("src", "scripts", "health_store.py"))


def _snap(ts=100.0, level="green", a="serving", b="idle", timer_push="ok"):
    return {"ts": ts, "health_level": level, "health_reasons": [],
            "feed_a_state": a, "feed_a_down": 0, "feed_a_stint": 1,
            "feed_b_state": b, "feed_b_down": 0, "feed_b_stint": 2,
            "pov_state": None, "obs_reachable": 1,
            "source_last_ok_age_s": 2.0, "source_count": 5,
            "cookies_present": 1, "cookies_age_h": 3.0, "cookies_stale": 0,
            "timer_mode": "running", "timer_push": timer_push,
            "mode": "race", "live_feed": "A", "live_stint": 1}


def t_open_migrate_sets_version_and_wal():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db"))
        try:
            hs.migrate(conn)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()


def t_migrate_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "h.db")
        first = hs.open_db(path)
        hs.migrate(first)
        first.close()
        conn = hs.open_db(path)
        try:
            hs.migrate(conn)            # second run must not raise
            assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
        finally:
            conn.close()


def t_record_then_query_roundtrip_parses_reasons():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            hs.record(conn, _snap(ts=100.0, level="yellow") | {"health_reasons": ["cookies stale"]}, "event")
            rows = hs.query_range(conn, 0, 1e12)
            assert len(rows) == 1
            assert rows[0]["ts"] == 100.0 and rows[0]["kind"] == "event"
            assert rows[0]["health_level"] == "yellow"
            assert rows[0]["health_reasons"] == ["cookies stale"]   # parsed back to a list
            assert rows[0]["pov_state"] is None
        finally:
            conn.close()


def t_query_range_filters_and_orders():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            for ts in (300.0, 100.0, 200.0):
                hs.record(conn, _snap(ts=ts), "periodic")
            rows = hs.query_range(conn, 150.0, 250.0)
            assert [r["ts"] for r in rows] == [200.0]
            allrows = hs.query_range(conn, 0, 1e12)
            assert [r["ts"] for r in allrows] == [100.0, 200.0, 300.0]   # ascending
        finally:
            conn.close()


def t_schema_has_no_url_columns():
    # Redaction by construction: a stream URL / sheet_id must never be storable.
    cols = " ".join(hs.COLUMNS).lower()
    for forbidden in ("url", "channel", "sheet_id", "http"):
        assert forbidden not in cols, forbidden


def t_collapse_bands_merges_equal_and_splits_on_change():
    pts = [(0.0, "green"), (30.0, "green"), (60.0, "red"), (90.0, "red")]
    bands = hs.collapse_bands(pts, gap_s=95)
    assert bands == [{"from": 0.0, "to": 30.0, "state": "green"},
                     {"from": 60.0, "to": 90.0, "state": "red"}]


def t_collapse_bands_breaks_on_gap_even_if_equal():
    # A long gap (relay down) must end the band, not bridge it.
    pts = [(0.0, "green"), (30.0, "green"), (1000.0, "green")]
    bands = hs.collapse_bands(pts, gap_s=95)
    assert len(bands) == 2
    assert bands[0] == {"from": 0.0, "to": 30.0, "state": "green"}
    assert bands[1] == {"from": 1000.0, "to": 1000.0, "state": "green"}


def t_derive_bands_covers_all_band_fields():
    samples = [_snap(ts=0.0, level="green", a="serving"),
               _snap(ts=30.0, level="red", a="idle")]
    bands = hs.derive_bands(samples)
    assert set(bands) == set(hs.BAND_FIELDS)
    assert bands["health_level"] == [{"from": 0.0, "to": 0.0, "state": "green"},
                                     {"from": 30.0, "to": 30.0, "state": "red"}]
    assert bands["feed_a_state"][0]["state"] == "serving"


def t_timer_push_is_a_band_field_and_transitions():
    assert "timer_push" in hs.BAND_FIELDS
    samples = [_snap(ts=0.0, timer_push="ok"), _snap(ts=30.0, timer_push="failed")]
    bands = hs.derive_bands(samples)["timer_push"]
    assert [b["state"] for b in bands] == ["ok", "failed"]


def t_derive_incidents_from_non_green_health_bands():
    samples = [
        _snap(ts=0.0, level="green"),
        _snap(ts=30.0, level="yellow") | {"health_reasons": ["cookies stale"]},
        _snap(ts=60.0, level="yellow") | {"health_reasons": ["cookies stale"]},
        _snap(ts=90.0, level="green"),
        _snap(ts=120.0, level="red") | {"health_reasons": ["Feed B down — lost the live stream"]},
    ]
    inc = hs.derive_incidents(samples)
    assert len(inc) == 2
    assert inc[0]["severity"] == "yellow"
    assert inc[0]["ts"] == 30.0 and inc[0]["end"] == 90.0 and inc[0]["duration_s"] == 60.0
    assert inc[0]["label"] == "cookies stale"
    assert inc[1]["severity"] == "red"
    assert inc[1]["label"].startswith("Feed B down")


def t_derive_incidents_empty_when_all_green():
    assert hs.derive_incidents([_snap(ts=0.0), _snap(ts=30.0)]) == []


def t_downsample_passthrough_when_small():
    pairs = [(0.0, 1.0), (1.0, 2.0)]
    assert hs.downsample(pairs, 10) == pairs


def t_downsample_buckets_to_max_keeping_last_per_bucket():
    pairs = [(float(i), float(i)) for i in range(10)]
    out = hs.downsample(pairs, 5)
    assert len(out) <= 5
    assert out[-1] == (9.0, 9.0)             # newest point always kept


def t_numeric_series_drops_none_and_splits_t_v():
    samples = [_snap(ts=0.0) | {"cookies_age_h": None},
               _snap(ts=30.0) | {"cookies_age_h": 4.0}]
    series = hs.numeric_series(samples)
    assert set(series) == set(hs.NUMERIC_FIELDS)
    assert {"source_last_ok_age_s", "cookies_age_h"}.issubset(set(hs.NUMERIC_FIELDS))
    assert series["cookies_age_h"] == {"t": [30.0], "v": [4.0]}   # None dropped
    assert series["source_last_ok_age_s"]["t"] == [0.0, 30.0]


def t_prune_deletes_older_than_retention():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            now = 10_000_000.0
            old = now - 40 * 86400        # 40 days old
            recent = now - 1 * 86400      # 1 day old
            hs.record(conn, _snap(ts=old), "periodic")
            hs.record(conn, _snap(ts=recent), "periodic")
            deleted = hs.prune(conn, retention_days=30, now=now)
            assert deleted == 1
            rows = hs.query_range(conn, 0, 1e12)
            assert [r["ts"] for r in rows] == [recent]
        finally:
            conn.close()


def t_export_then_import_into_fresh_db_roundtrips():
    with tempfile.TemporaryDirectory() as d:
        a = hs.open_db(os.path.join(d, "a.db")); hs.migrate(a)
        b = hs.open_db(os.path.join(d, "b.db")); hs.migrate(b)
        try:
            hs.record(a, _snap(ts=100.0, level="green"), "periodic")
            hs.record(a, _snap(ts=130.0, level="red"), "event")
            lines = hs.export_jsonl(a)
            assert len(lines) == 2 and lines[0].startswith("{")

            n = hs.import_jsonl(b, lines)
            assert n == 2
            rows = hs.query_range(b, 0, 1e12)
            assert [r["ts"] for r in rows] == [100.0, 130.0]
            assert rows[1]["kind"] == "event"
        finally:
            a.close(); b.close()


def t_import_is_idempotent_dedup_by_ts_kind():
    with tempfile.TemporaryDirectory() as d:
        a = hs.open_db(os.path.join(d, "a.db")); hs.migrate(a)
        try:
            hs.record(a, _snap(ts=100.0), "periodic")
            lines = hs.export_jsonl(a)
            assert hs.import_jsonl(a, lines) == 0       # re-importing changes nothing
            assert len(hs.query_range(a, 0, 1e12)) == 1
        finally:
            a.close()


def t_import_skips_malformed_lines():
    with tempfile.TemporaryDirectory() as d:
        b = hs.open_db(os.path.join(d, "b.db")); hs.migrate(b)
        try:
            good = hs.export_jsonl_line({"ts": 5.0, "kind": "periodic", "health_level": "green",
                                         "health_reasons": []})
            n = hs.import_jsonl(b, [good, "{not json", "", "null", "[]"])
            assert n == 1
        finally:
            b.close()


def t_migrate_v2_to_v3_is_lossless():
    import tempfile, os, sqlite3
    d = tempfile.mkdtemp()
    path = os.path.join(d, "h.db")
    # Build a v2 table by hand (old column set + user_version=2), insert one row.
    c = sqlite3.connect(path)
    try:
        c.executescript(
            "CREATE TABLE samples (ts REAL NOT NULL, kind TEXT NOT NULL, "
            "health_level TEXT, health_reasons TEXT, feed_a_state TEXT, "
            "feed_a_down INTEGER, feed_a_stint INTEGER, feed_b_state TEXT, "
            "feed_b_down INTEGER, feed_b_stint INTEGER, pov_state TEXT, "
            "obs_reachable INTEGER, source_last_ok_age_s REAL, source_count INTEGER, "
            "cookies_present INTEGER, cookies_age_h REAL, cookies_stale INTEGER, "
            "timer_mode TEXT, timer_push TEXT, mode TEXT, live_feed TEXT, "
            "live_stint INTEGER);")
        c.execute("PRAGMA user_version=2")
        c.execute("INSERT INTO samples (ts, kind, health_level) VALUES (?,?,?)",
                  (1000.0, "tick", "green"))
        c.commit()
    finally:
        c.close()
    # Migrate via the real loader and assert the upgrade is lossless.
    conn = hs.open_db(path)
    try:
        hs.migrate(conn)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == hs.SCHEMA_VERSION, ver
        cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
        for f in ("stream_active", "funnel_ok", "stream_kbps", "obs_cpu_pct",
                  "feed_a_quality", "pov_quality"):
            assert f in cols, f
        rows = conn.execute("SELECT ts, health_level, stream_active FROM samples").fetchall()
        assert rows[0][0] == 1000.0 and rows[0][1] == "green" and rows[0][2] is None
    finally:
        conn.close()


def t_new_fields_round_trip():
    import tempfile, os
    d = tempfile.mkdtemp()
    conn = hs.open_db(os.path.join(d, "h.db"))
    try:
        hs.migrate(conn)
        snap = {"ts": 5.0, "health_level": "green", "stream_active": 1,
                "stream_reconnecting": 0, "funnel_ok": 1, "sheet_push_ok": 0,
                "tailscale_up": 1, "companion_ok": 0, "stream_kbps": 6200.0,
                "stream_dropped_pct": 0.5, "stream_congestion": 0.1,
                "obs_cpu_pct": 12.3, "obs_mem_mb": 900.0, "obs_fps": 60.0,
                "obs_render_skipped_pct": 0.0, "obs_disk_free_mb": 50000.0,
                "feed_a_quality": "720p", "feed_b_quality": "1080p",
                "pov_quality": "source"}
        hs.record(conn, snap, "tick")
        got = hs.query_range(conn, 0, 10)[0]
        assert got["stream_active"] == 1 and got["feed_a_quality"] == "720p"
        assert got["stream_kbps"] == 6200.0 and got["sheet_push_ok"] == 0
        bands = hs.derive_bands([got])
        assert "funnel_ok" in bands and "companion_ok" in bands
        series = hs.numeric_series([got])
        assert "stream_kbps" in series and "obs_cpu_pct" in series
    finally:
        conn.close()


def t_migrate_creates_events_table_and_bumps_version():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
            assert hs.SCHEMA_VERSION >= 4
            cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
            assert {"ts", "type", "label", "producer", "metadata"} <= cols, cols
        finally:
            conn.close()


def t_record_event_then_query_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            hs.record_event(conn, 100.0, "takeover", label="B took over",
                            producer="Bob", metadata={"from": "Alice", "stint": 7})
            hs.record_event(conn, 50.0, "obs_stream_start", label="started",
                            producer="Bob")
            rows = hs.query_events(conn, 0, 1e12)
            assert [r["ts"] for r in rows] == [50.0, 100.0]            # ascending
            tk = rows[1]
            assert tk["type"] == "takeover" and tk["producer"] == "Bob"
            assert tk["label"] == "B took over"
            assert tk["metadata"] == {"from": "Alice", "stint": 7}     # JSON parsed back
            assert rows[0]["metadata"] is None                         # no metadata -> None
        finally:
            conn.close()


def t_query_events_filters_window():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            for ts in (10.0, 100.0, 200.0):
                hs.record_event(conn, ts, "obs_stream_stop")
            assert [r["ts"] for r in hs.query_events(conn, 50.0, 150.0)] == [100.0]
        finally:
            conn.close()


def t_events_ride_along_export_import_and_dedup():
    with tempfile.TemporaryDirectory() as d:
        a = hs.open_db(os.path.join(d, "a.db")); hs.migrate(a)
        b = hs.open_db(os.path.join(d, "b.db")); hs.migrate(b)
        try:
            hs.record(a, _snap(ts=100.0, level="green"), "periodic")
            hs.record_event(a, 120.0, "takeover", label="x", producer="Bob",
                            metadata={"stint": 3})
            lines = hs.export_jsonl(a)                       # samples + tagged event lines
            assert any('"_kind": "event"' in ln for ln in lines), lines
            n = hs.import_jsonl(b, lines)
            assert n == 2                                    # one sample + one event
            assert [r["ts"] for r in hs.query_range(b, 0, 1e12)] == [100.0]
            ev = hs.query_events(b, 0, 1e12)
            assert len(ev) == 1 and ev[0]["type"] == "takeover"
            assert ev[0]["metadata"] == {"stint": 3}
            assert hs.import_jsonl(b, lines) == 0            # re-import is a no-op (dedup)
        finally:
            a.close(); b.close()


def t_export_jsonl_orders_samples_before_events():
    import json as _json
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            hs.record(conn, _snap(ts=100.0), "periodic")
            hs.record_event(conn, 50.0, "obs_stream_start")
            kinds = [(_json.loads(ln).get("_kind") or "sample") for ln in hs.export_jsonl(conn)]
            assert kinds == ["sample", "event"], kinds   # samples first, events appended
        finally:
            conn.close()


def t_v5_sys_columns_present_and_charted():
    cols = set(hs.COLUMNS)
    for c in ("sys_cpu_pct", "sys_mem_pct", "sys_net_up_kbps",
              "sys_net_down_kbps", "sys_disk_free_mb"):
        assert c in cols, c
        assert c in hs.NUMERIC_FIELDS, c
    assert hs.SCHEMA_VERSION >= 5


def t_v5_migration_adds_sys_columns_losslessly():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "old.db")
        # simulate a pre-v5 DB: create samples WITHOUT the sys_* columns, one row
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE samples (ts REAL NOT NULL, kind TEXT NOT NULL, "
                     "health_level TEXT)")
        conn.execute("INSERT INTO samples (ts, kind, health_level) VALUES (1.0,'periodic','green')")
        conn.commit(); conn.close()
        conn = hs.open_db(path)
        hs.migrate(conn)                                  # must add sys_* without loss
        try:
            have = {r["name"] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
            assert {"sys_cpu_pct", "sys_disk_free_mb"} <= have, have
            rows = conn.execute("SELECT ts, health_level FROM samples").fetchall()
            assert len(rows) == 1 and rows[0]["health_level"] == "green"   # old row survived
            assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
        finally:
            conn.close()


def t_v5_sys_fields_roundtrip_and_series():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            hs.record(conn, _snap(ts=0.0) | {"sys_cpu_pct": 40.0, "sys_mem_pct": 55.0,
                                             "sys_net_up_kbps": 2.0, "sys_net_down_kbps": 1.0,
                                             "sys_disk_free_mb": 100.0}, "periodic")
            hs.record(conn, _snap(ts=30.0) | {"sys_cpu_pct": 60.0}, "periodic")
            rows = hs.query_range(conn, 0, 1e12)
            assert rows[0]["sys_cpu_pct"] == 40.0 and rows[0]["sys_disk_free_mb"] == 100.0
            series = hs.numeric_series(rows)
            assert series["sys_cpu_pct"]["v"] == [40.0, 60.0], series["sys_cpu_pct"]
        finally:
            conn.close()


m = _load("irofeeds", ("src", "relay", "racecast-feeds.py"))

LOGDIR = tempfile.mkdtemp(prefix="racecast-test-health-")
_URLS = [f"https://www.youtube.com/watch?v=stint{i}" for i in range(1, 3)]


class _FakeSrc:
    """Minimal schedule source for _make_relay — two stints, no live pulls."""
    def __init__(self, items): self.items = list(items)
    def get(self): return self.items
    def get_rows(self): return [(u, "", "", i + 1) for i, u in enumerate(self.items)]
    def refresh(self, timeout=None): pass
    def health(self): return {"last_ok_age_s": 1.0, "count": len(self.items)}


def _make_relay(mod):
    """Build a minimal Relay with two stints and a temp log dir."""
    src = _FakeSrc(_URLS)
    return mod.Relay(src, [53001, 53002], LOGDIR)


def t_healthstore_record_tick_marks_changes_as_events():
    with tempfile.TemporaryDirectory() as d:
        store = m.HealthStore(os.path.join(d, "h.db"))
        try:
            r1 = store.record_tick(_snap(ts=0.0, level="green"), now=0.0)
            r2 = store.record_tick(_snap(ts=30.0, level="green"), now=30.0)
            r3 = store.record_tick(_snap(ts=60.0, level="red"), now=60.0)
            assert r1["kind"] == "event"        # first row is always an event (baseline)
            assert r2["kind"] == "periodic"     # unchanged state
            assert r3["kind"] == "event"        # health_level changed
            assert len(store.query(0, 1e12)) == 3
        finally:
            store.close()


def t_healthstore_bands_incidents_series_smoke():
    with tempfile.TemporaryDirectory() as d:
        store = m.HealthStore(os.path.join(d, "h.db"))
        try:
            store.record_tick(_snap(ts=0.0, level="green"), now=0.0)
            store.record_tick(_snap(ts=30.0, level="red") | {"health_reasons": ["Feed B down"]}, now=30.0)
            assert store.bands(0, 1e12)["health_level"][-1]["state"] == "red"
            assert store.incidents(0, 1e12)[0]["severity"] == "red"
            assert "source_last_ok_age_s" in store.series(0, 1e12, 2000)
        finally:
            store.close()


def t_relay_health_snapshot_has_no_urls_and_all_columns():
    relay = _make_relay(m)
    snap = relay._health_snapshot(now=123.0)
    for col in hs.COLUMNS:
        if col in ("ts", "kind"):
            continue
        assert col in snap, col
    assert "timer_push" in snap
    assert "timer_remaining_s" not in snap   # clean break: replaced by timer_push
    blob = repr(snap).lower()
    assert "http" not in blob and "youtu" not in blob   # redaction


def t_healthstore_wrapper_record_event_and_query():
    with tempfile.TemporaryDirectory() as d:
        store = m.HealthStore(os.path.join(d, "h.db"))
        try:
            store.record_event(120.0, "takeover", label="B took over", producer="Bob",
                               metadata={"stint": 4})
            evs = store.events(0, 1e12)
            assert len(evs) == 1 and evs[0]["type"] == "takeover"
            assert evs[0]["producer"] == "Bob" and evs[0]["metadata"] == {"stint": 4}
        finally:
            store.close()


def t_annotate_latest_event():
    with tempfile.TemporaryDirectory() as d:
        conn = hs.open_db(os.path.join(d, "h.db")); hs.migrate(conn)
        try:
            hs.record_event(conn, 100.0, "feed_substitution", metadata={"feed": "A", "stint": 2})
            hs.record_event(conn, 200.0, "feed_substitution", metadata={"feed": "B", "stint": 4})
            hs.record_event(conn, 300.0, "takeover", metadata={"stint": 5})
            out = hs.annotate_latest_event(conn, "feed_substitution", {"reason": "A dropped"})
            assert out["ts"] == 200.0 and out["metadata"] == {"feed": "B", "stint": 4, "reason": "A dropped"}
            # only the latest substitution got the reason; the earlier one is untouched
            evs = hs.query_events(conn, 0, 1e12)
            subs = [e for e in evs if e["type"] == "feed_substitution"]
            assert subs[0]["metadata"] == {"feed": "A", "stint": 2}       # no reason
            assert subs[1]["metadata"].get("reason") == "A dropped"
            # the takeover event is untouched
            assert [e for e in evs if e["type"] == "takeover"][0]["metadata"] == {"stint": 5}
            # None when no such event
            assert hs.annotate_latest_event(conn, "nope", {"reason": "x"}) is None
        finally:
            conn.close()


def t_healthstore_wrapper_annotate_latest_event():
    with tempfile.TemporaryDirectory() as d:
        store = m.HealthStore(os.path.join(d, "h.db"))
        try:
            store.record_event(100.0, "feed_substitution", metadata={"feed": "A"})
            store.record_event(200.0, "feed_substitution", metadata={"feed": "B"})
            out = store.annotate_latest_event("feed_substitution", {"reason": "A dropped"})
            assert out["ts"] == 200.0
            assert out["metadata"] == {"feed": "B", "reason": "A dropped"}
            evs = store.events(0, 1e12)
            assert evs[0]["metadata"] == {"feed": "A"}          # earlier event untouched
        finally:
            store.close()


def t_incident_recovery_duration():
    def s(ts, lvl):
        return {"ts": ts, "health_level": lvl, "health_reasons": ["off air"] if lvl == "red" else []}
    # single-sample red between greens -> lasts until recovery (30s), not 0s
    inc = hs.derive_incidents([s(1000, "green"), s(1030, "red"), s(1060, "green")])
    assert len(inc) == 1
    assert inc[0]["ts"] == 1030 and inc[0]["end"] == 1060 and inc[0]["duration_s"] == 30
    # multi-sample red -> extends to the recovering sample
    inc = hs.derive_incidents([s(1000, "green"), s(1030, "red"), s(1060, "red"), s(1090, "green")])
    assert inc[0]["duration_s"] == 60 and inc[0]["end"] == 1090
    # trailing red with no recovery -> extend by one interval (not 0)
    inc = hs.derive_incidents([s(1000, "green"), s(1030, "red")])
    assert inc[0]["duration_s"] == hs.SAMPLE_INTERVAL_S and inc[0]["end"] == 1030 + hs.SAMPLE_INTERVAL_S
    # never bridge a relay-down hole (> GAP_S) to the next band
    inc = hs.derive_incidents([s(1000, "red"), s(1000 + hs.GAP_S + 100, "green")])
    assert inc[0]["duration_s"] == hs.SAMPLE_INTERVAL_S


def t_migrate_adds_desync_active_v6_lossless():
    import tempfile, os, sqlite3
    d = tempfile.mkdtemp()
    path = os.path.join(d, "h.db")
    # A v5 DB by hand: full column set MINUS desync_active, user_version=5, one row.
    c = sqlite3.connect(path)
    try:
        c.executescript(
            "CREATE TABLE samples (ts REAL NOT NULL, kind TEXT NOT NULL, "
            "health_level TEXT, live_stint INTEGER);")   # minimal legacy subset
        c.execute("PRAGMA user_version=5")
        c.execute("INSERT INTO samples (ts, kind, health_level, live_stint) "
                  "VALUES (?,?,?,?)", (1000.0, "tick", "green", 7))
        c.commit()
    finally:
        c.close()
    conn = hs.open_db(path)
    try:
        hs.migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
        assert "desync_active" in cols
        row = conn.execute("SELECT live_stint, desync_active FROM samples").fetchone()
        assert row[0] == 7 and row[1] is None      # legacy row lossless, new col NULL
    finally:
        conn.close()


def t_desync_active_round_trips():
    import tempfile, os
    d = tempfile.mkdtemp()
    conn = hs.open_db(os.path.join(d, "h.db"))
    try:
        hs.migrate(conn)
        hs.record(conn, {"ts": 5.0, "health_level": "green", "desync_active": 1}, "tick")
        got = hs.query_range(conn, 0, 10)[0]
        assert got["desync_active"] == 1, got
    finally:
        conn.close()


def t_migrate_adds_render_skip_rate_v7_lossless_and_charted():
    import tempfile, os, sqlite3
    d = tempfile.mkdtemp()
    path = os.path.join(d, "h.db")
    c = sqlite3.connect(path)
    try:
        c.executescript("CREATE TABLE samples (ts REAL NOT NULL, kind TEXT NOT NULL, "
                        "health_level TEXT, obs_render_skipped_pct REAL);")
        c.execute("PRAGMA user_version=6")
        c.execute("INSERT INTO samples (ts, kind, obs_render_skipped_pct) VALUES (?,?,?)",
                  (1000.0, "tick", 0.3))
        c.commit()
    finally:
        c.close()
    conn = hs.open_db(path)
    try:
        hs.migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
        assert "obs_render_skip_rate_pct" in cols
        row = conn.execute("SELECT obs_render_skipped_pct, obs_render_skip_rate_pct "
                           "FROM samples").fetchone()
        assert row[0] == 0.3 and row[1] is None      # lossless upgrade; new col NULL
        assert "obs_render_skip_rate_pct" in hs.NUMERIC_FIELDS   # a charted #488 drift series
    finally:
        conn.close()
    # round-trip on a FRESH (full-schema) migrated DB
    conn2 = hs.open_db(os.path.join(d, "fresh.db"))
    try:
        hs.migrate(conn2)
        hs.record(conn2, {"ts": 5.0, "obs_render_skip_rate_pct": 4.8}, "tick")
        got = hs.query_range(conn2, 0, 10)[0]
        assert got["obs_render_skip_rate_pct"] == 4.8
    finally:
        conn2.close()


def t_max_gap_columns_round_trip():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    conn = hs.open_db(_os.path.join(d, "h.db"))
    try:
        hs.migrate(conn)
        hs.record(conn, {"ts": 5.0, "health_level": "green",
                         "feed_a_max_gap_s": 2.5, "feed_b_max_gap_s": 0.0}, "tick")
        got = hs.query_range(conn, 0, 10)[0]
        assert got["feed_a_max_gap_s"] == 2.5 and got["feed_b_max_gap_s"] == 0.0
        series = hs.numeric_series([got])
        assert "feed_a_max_gap_s" in series           # NUMERIC_FIELDS wired
    finally:
        conn.close()



def t_migrate_adds_backlog_columns_v9_lossless_and_charted():
    import sqlite3
    d = tempfile.mkdtemp()
    path = os.path.join(d, "h.db")
    c = sqlite3.connect(path)
    try:
        c.executescript("CREATE TABLE samples (ts REAL NOT NULL, kind TEXT NOT NULL, "
                        "health_level TEXT, feed_a_max_gap_s REAL);")
        c.execute("PRAGMA user_version=8")
        c.execute("INSERT INTO samples (ts, kind, feed_a_max_gap_s) VALUES (?,?,?)",
                  (1000.0, "tick", 2.5))
        c.commit()
    finally:
        c.close()
    conn = hs.open_db(path)
    try:
        hs.migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
        assert {"feed_a_backlog_s", "feed_b_backlog_s", "pov_backlog_s"} <= cols, cols
        row = conn.execute("SELECT feed_a_max_gap_s, feed_a_backlog_s, feed_b_backlog_s, "
                           "pov_backlog_s FROM samples").fetchone()
        assert tuple(row) == (2.5, None, None, None)    # lossless; new columns NULL
    finally:
        conn.close()
    conn2 = hs.open_db(os.path.join(d, "fresh.db"))
    try:
        hs.migrate(conn2)
        hs.record(conn2, {"ts": 2000.0, "feed_a_backlog_s": 11.6, "feed_b_backlog_s": 3.1,
                          "pov_backlog_s": 0.4}, "tick")
        got = hs.query_range(conn2, 1500, 2500)[0]
        assert (got["feed_a_backlog_s"], got["feed_b_backlog_s"], got["pov_backlog_s"]) == \
            (11.6, 3.1, 0.4)
        series = hs.numeric_series([got])
        for f in ("feed_a_backlog_s", "feed_b_backlog_s", "pov_backlog_s"):
            assert f in series, f                    # NUMERIC_FIELDS wired
    finally:
        conn2.close()


def t_migrate_adds_fps_target_column_v10_lossless():
    # #586: the configured OBS frame rate, the reference the report judges obs_fps by.
    import sqlite3
    d = tempfile.mkdtemp()
    path = os.path.join(d, "h.db")
    c = sqlite3.connect(path)
    try:
        c.executescript("CREATE TABLE samples (ts REAL NOT NULL, kind TEXT NOT NULL, "
                        "obs_fps REAL, feed_a_backlog_s REAL);")
        c.execute("PRAGMA user_version=9")
        c.execute("INSERT INTO samples (ts, kind, obs_fps) VALUES (?,?,?)", (1000.0, "tick", 45.8))
        c.commit()
    finally:
        c.close()
    conn = hs.open_db(path)
    try:
        hs.migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == hs.SCHEMA_VERSION == 10
        cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
        assert "obs_fps_target" in cols, cols
        row = conn.execute("SELECT obs_fps, obs_fps_target FROM samples").fetchone()
        assert tuple(row) == (45.8, None)               # lossless; new column NULL
    finally:
        conn.close()
    conn2 = hs.open_db(os.path.join(d, "fresh.db"))
    try:
        hs.migrate(conn2)
        hs.record(conn2, {"ts": 2000.0, "obs_fps": 59.9, "obs_fps_target": 60.0}, "tick")
        got = hs.query_range(conn2, 1500, 2500)[0]
        assert (got["obs_fps"], got["obs_fps_target"]) == (59.9, 60.0)
    finally:
        conn2.close()


def t_backlog_rule_is_shared_with_the_relay_definition():
    # #586: the report's verdict and the relay's yellow reason apply one definition.
    assert hs.feed_backlog_degraded(8.0, 3.0, 5.0) is False     # exactly at the threshold
    assert hs.feed_backlog_degraded(8.1, 3.0, 5.0) is True
    assert hs.feed_backlog_degraded(None, 3.0, 5.0) is False
    assert hs.feed_prebuffer_s({}) == hs.DEFAULT_FEED_PREBUFFER_S == 3.0
    assert hs.feed_backlog_warn_s({"RACECAST_FEED_BACKLOG_WARN_S": "8"}) == 8.0
    assert hs.feed_backlog_warn_s({}) == hs.FEED_BACKLOG_WARN_S == 5.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

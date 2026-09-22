#!/usr/bin/env python3
"""Stdlib checks for the Ookla speed-test helpers. Run: python3 tests/test_speedtest.py"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import speedtest as m   # noqa: E402
import preflight as pf  # noqa: E402

# A captured `speedtest --format=json` payload:
#   download.bandwidth 6_000_000 B/s -> 48.0 Mbps; upload 2_750_000 -> 22.0 Mbps
OOKLA_JSON = json.dumps({
    "type": "result",
    "ping": {"jitter": 1.2, "latency": 11.5},
    "download": {"bandwidth": 6_000_000, "bytes": 50_000_000, "elapsed": 3000},
    "upload": {"bandwidth": 2_750_000, "bytes": 20_000_000, "elapsed": 3000},
    "packetLoss": 0,
    "isp": "Deutsche Telekom",
    "server": {"id": 1234, "name": "Telekom", "location": "Berlin"},
    "result": {"id": "abc", "url": "https://www.speedtest.net/result/c/abc"},
})


def t_run_argv_accepts_license_and_gdpr():
    argv = m.run_argv()
    assert argv[0] == "speedtest"
    assert "--format=json" in argv
    # Dropping these reintroduces the blocking first-run prompt.
    assert "--accept-license" in argv and "--accept-gdpr" in argv


def t_parse_result_converts_bandwidth_to_mbps():
    rec = m.parse_result(OOKLA_JSON, now=1_000_000)
    assert rec["ts"] == 1_000_000
    assert rec["download_mbps"] == 48.0
    assert rec["upload_mbps"] == 22.0
    assert rec["ping_ms"] == 11.5
    assert rec["jitter_ms"] == 1.2
    assert rec["packet_loss"] == 0.0
    assert rec["server"] == "Telekom — Berlin"
    assert rec["isp"] == "Deutsche Telekom"
    assert rec["result_url"] == "https://www.speedtest.net/result/c/abc"


def t_parse_result_tolerates_missing_optional_fields():
    rec = m.parse_result(json.dumps({
        "download": {"bandwidth": 3_125_000},   # 25.0 Mbps
        "upload": {"bandwidth": 1_250_000},     # 10.0 Mbps
    }), now=5)
    assert rec["download_mbps"] == 25.0 and rec["upload_mbps"] == 10.0
    assert rec["ping_ms"] is None and rec["packet_loss"] is None
    assert rec["result_url"] is None and rec["server"] == "" and rec["isp"] == ""


def t_parse_result_rejects_garbage():
    for bad in ("", "not json", json.dumps({"download": {"bandwidth": 1}})):
        try:
            m.parse_result(bad, now=1)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def _rec(ts, dl=30.0, ul=12.0):
    return {"ts": ts, "download_mbps": dl, "upload_mbps": ul, "ping_ms": 10.0,
            "jitter_ms": 1.0, "packet_loss": 0.0, "server": "S", "isp": "I",
            "result_url": None}


def t_history_roundtrip_and_latest(tmp=None):
    import tempfile
    d = tempfile.mkdtemp()
    assert m.load_latest(d) is None
    assert m.load_history(d) == []
    m.append_record(_rec(100), d)
    m.append_record(_rec(200), d)
    assert m.load_latest(d)["ts"] == 200            # newest
    hist = m.load_history(d)
    assert [r["ts"] for r in hist] == [200, 100]    # newest-first for the UI


def t_history_trims_to_limit():
    import tempfile
    d = tempfile.mkdtemp()
    for ts in range(1, 15):                          # 14 runs
        m.append_record(_rec(ts), d)
    hist = m.load_history(d)
    assert len(hist) == m.HISTORY_LIMIT == 10
    assert hist[0]["ts"] == 14 and hist[-1]["ts"] == 5   # only the last 10 kept


def t_history_skips_corrupt_lines():
    import tempfile
    d = tempfile.mkdtemp()
    with open(m.history_path(d), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_rec(1)) + "\n")
        fh.write("{ not json\n")                      # corrupt line ignored
        fh.write(json.dumps(_rec(2)) + "\n")
    assert [r["ts"] for r in m.load_history(d)] == [2, 1]
    assert m.load_latest(d)["ts"] == 2


def t_find_binary_prefers_path():
    assert m.find_binary("/any", which=lambda n: "/usr/bin/speedtest") == "/usr/bin/speedtest"


def t_find_binary_falls_back_to_managed_dir():
    import os as _os
    import tempfile
    d = tempfile.mkdtemp()
    bindir = m.managed_bin_dir(d)
    _os.makedirs(bindir)
    # find_binary looks for speedtest.exe on Windows, speedtest elsewhere.
    name = "speedtest.exe" if _os.name == "nt" else "speedtest"
    binpath = _os.path.join(bindir, name)
    with open(binpath, "w") as fh:
        fh.write("x")
    assert m.find_binary(d, which=lambda n: None) == binpath


def t_find_binary_none_when_nowhere():
    import tempfile
    assert m.find_binary(tempfile.mkdtemp(), which=lambda n: None) is None


def t_run_argv_accepts_explicit_binary():
    assert m.run_argv("/opt/x/speedtest")[0] == "/opt/x/speedtest"
    assert m.run_argv()[0] == "speedtest"   # default unchanged


NOW = 1_000_000           # fixed clock
DAY = 86_400


def t_classify_none_is_info():
    r = m.classify(None, now=NOW)
    assert r.level == pf.INFO and r.name == "bandwidth"
    assert "racecast speedtest" in r.detail


def t_classify_below_minimum_is_warn():
    r = m.classify(_rec(NOW, dl=20.0, ul=8.0), now=NOW)
    assert r.level == pf.WARN and "minimum" in r.detail


def t_classify_worse_side_governs():
    # download fine, upload below minimum -> WARN
    r = m.classify(_rec(NOW, dl=80.0, ul=8.0), now=NOW)
    assert r.level == pf.WARN


def t_classify_between_min_and_recommended_is_warn():
    r = m.classify(_rec(NOW, dl=48.0, ul=22.0), now=NOW)
    assert r.level == pf.WARN and "recommended" in r.detail


def t_classify_at_or_above_recommended_is_pass():
    r = m.classify(_rec(NOW, dl=55.0, ul=25.0), now=NOW)
    assert r.level == pf.PASS


def t_classify_stale_is_warn_regardless_of_value():
    old = _rec(NOW - 10 * DAY, dl=200.0, ul=100.0)   # great numbers, but 10 days old
    r = m.classify(old, now=NOW, max_age_days=7)
    assert r.level == pf.WARN and "stale" in r.detail


def t_default_runtime_dir_repo_layout():
    repo = os.path.join("X", "src", "scripts")
    assert m.default_runtime_dir(repo) == os.path.join("X", "runtime")
    assert m.default_runtime_dir("/some/dist/scripts") == "/some/dist/scripts"


def t_run_raises_when_binary_missing():
    try:
        m.run(now=NOW, runtime_dir="/tmp/nope", which=lambda n: None)
    except m.SpeedtestUnavailable as exc:
        assert "install-tools" in str(exc)
        return
    raise AssertionError("expected SpeedtestUnavailable")


def t_run_parses_and_appends(tmp=None):
    import tempfile
    d = tempfile.mkdtemp()

    class _Proc:
        returncode = 0
        stdout = OOKLA_JSON
        stderr = ""

    rec = m.run(now=NOW, runtime_dir=d,
                runner=lambda *a, **k: _Proc(), which=lambda n: "/usr/bin/speedtest")
    assert rec["download_mbps"] == 48.0
    assert m.load_latest(d)["ts"] == NOW           # persisted


def t_run_raises_on_nonzero_exit():
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "Configuration - Could not retrieve"
    try:
        m.run(now=NOW, runtime_dir="/tmp/x",
              runner=lambda *a, **k: _Proc(), which=lambda n: "/usr/bin/speedtest")
    except m.SpeedtestFailed:
        return
    raise AssertionError("expected SpeedtestFailed")


def t_render_contains_key_lines():
    rec = m.parse_result(OOKLA_JSON, now=NOW)
    text = m.render(rec, now=NOW)
    assert "Download  48.0 Mbps" in text
    assert "Upload    22.0 Mbps" in text
    assert "speedtest.net/result" in text


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""Opt-in Ookla bandwidth speed test for the GT Racing broadcast setup.

Wraps the Ookla `speedtest` CLI: runs it, parses its --format=json output into a
small record, appends that record to a machine-level JSONL history (trimmed to the
last HISTORY_LIMIT runs), and classifies the latest record against the documented
minimum/recommended bandwidth for `racecast preflight`.

NEVER runs automatically: it is invoked only by `racecast speedtest` and the
Control Center button. Pure Python 3 standard library (the `speedtest` binary is
the only external dependency, installed via `racecast install-tools`).
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections import namedtuple

# Level constants + a Result shape compatible with preflight.Result (same
# .level/.name/.detail fields, identical level strings). Defined locally rather
# than imported from preflight so this module does NOT depend on preflight:
# preflight imports US (lazily, in gather()), and importing it back would be a cycle.
PASS, WARN, INFO = "PASS", "WARN", "INFO"
Result = namedtuple("Result", ("level", "name", "detail"))

SPEEDTEST_BIN = "speedtest"
# Thresholds mirror src/docs/wiki/Set-up-the-broadcast-PC.md (the single source).
MIN_DOWN_MBPS, MIN_UP_MBPS = 25.0, 10.0
REC_DOWN_MBPS, REC_UP_MBPS = 50.0, 20.0
DEFAULT_MAX_AGE_DAYS = 7
HISTORY_LIMIT = 10
HISTORY_NAME = "speedtest-history.jsonl"


class SpeedtestUnavailable(RuntimeError):
    """The Ookla speedtest CLI could not be found (PATH or the managed bin dir)."""


class SpeedtestFailed(RuntimeError):
    """The speedtest CLI ran but did not produce a usable result."""


def managed_bin_dir(runtime_dir):
    """Where install-tools drops the direct-downloaded Ookla binary on
    macOS/Linux (Windows installs it via winget, onto PATH)."""
    return os.path.join(runtime_dir, "bin")


def find_binary(runtime_dir, which=shutil.which):
    """Resolve the speedtest binary. PATH first (winget / brew / manual installs),
    then the racecast-managed bin dir (the mac/Linux direct-download install).
    Returns the path, or None when it is installed nowhere."""
    hit = which(SPEEDTEST_BIN)
    if hit:
        return hit
    name = SPEEDTEST_BIN + (".exe" if os.name == "nt" else "")
    cand = os.path.join(managed_bin_dir(runtime_dir), name)
    return cand if os.path.isfile(cand) else None


def run_argv(binary=SPEEDTEST_BIN):
    """argv for one measurement. The --accept-* flags are passed on EVERY run so
    the CLI's interactive first-run license/GDPR prompt never blocks us."""
    return [binary, "--format=json", "--accept-license", "--accept-gdpr"]


def _mbps(bandwidth_bytes_per_sec):
    """Ookla reports bandwidth in BYTES per second -> Mbps."""
    return round(float(bandwidth_bytes_per_sec) * 8 / 1_000_000, 1)


def _round_or_none(value):
    return None if value is None else round(float(value), 1)


def parse_result(json_text, now):
    """Parse a `speedtest --format=json` payload into a persisted record.
    Raises ValueError on malformed JSON or a missing download/upload section."""
    data = json.loads(json_text)            # ValueError on bad JSON
    if not isinstance(data, dict) or "download" not in data or "upload" not in data:
        raise ValueError("unexpected speedtest JSON (no download/upload section)")
    ping = data.get("ping") or {}
    server = data.get("server") or {}
    result = data.get("result") or {}
    server_str = " — ".join(s for s in (server.get("name"), server.get("location")) if s)
    return {
        "ts": int(now),
        "download_mbps": _mbps(data["download"]["bandwidth"]),
        "upload_mbps": _mbps(data["upload"]["bandwidth"]),
        "ping_ms": _round_or_none(ping.get("latency")),
        "jitter_ms": _round_or_none(ping.get("jitter")),
        "packet_loss": _round_or_none(data.get("packetLoss")),
        "server": server_str,
        "isp": data.get("isp") or "",
        "result_url": result.get("url") or None,
    }


def history_path(runtime_dir):
    return os.path.join(runtime_dir, HISTORY_NAME)


def _read_all(runtime_dir):
    """All valid records in file (chronological) order. Tolerates a missing file
    and skips individual corrupt lines so one bad write can't lose the history."""
    out = []
    try:
        with open(history_path(runtime_dir), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue   # skip a corrupt line, keep the rest
    except FileNotFoundError:
        return []
    return out


def load_history(runtime_dir, limit=HISTORY_LIMIT):
    """Up to `limit` recent records, NEWEST-FIRST (for the Control Center table)."""
    recent = _read_all(runtime_dir)[-limit:]
    recent.reverse()
    return recent


def load_latest(runtime_dir):
    """The most recent record, or None when nothing has been measured yet."""
    recs = _read_all(runtime_dir)
    return recs[-1] if recs else None


def append_record(record, runtime_dir):
    """Append one record and rewrite the file trimmed to the last HISTORY_LIMIT.
    Writes to a temp file and os.replace()s it in, the atomic-rewrite pattern the
    rest of the project uses (chat_admin/backup_admin/profile_io), so a crash
    mid-write can't truncate the history."""
    os.makedirs(runtime_dir, exist_ok=True)
    kept = (_read_all(runtime_dir) + [record])[-HISTORY_LIMIT:]
    fd, tmp = tempfile.mkstemp(dir=runtime_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for rec in kept:
                fh.write(json.dumps(rec) + "\n")
        os.replace(tmp, history_path(runtime_dir))
    except BaseException:   # never leave a temp file behind on any failure
        os.unlink(tmp)
        raise
    return record


def _fmt_age(age_days):
    if age_days < 1 / 24:
        return "just now"
    if age_days < 1:
        return f"{int(age_days * 24)} h ago"
    return f"{int(age_days)} d ago"


def _summary(record, age_days):
    parts = [f"↓{record.get('download_mbps', 0.0):.1f} "
             f"↑{record.get('upload_mbps', 0.0):.1f} Mbps",
             f"measured {_fmt_age(age_days)}"]
    if record.get("server"):
        parts.append(record["server"])
    if record.get("isp"):
        parts.append(record["isp"])
    return " · ".join(parts)


def classify(record, now, max_age_days=DEFAULT_MAX_AGE_DAYS):
    """Latest record -> a preflight.Result. Below-minimum and stale both WARN
    (never FAIL); the worse of download/upload governs the level."""
    if not record:
        return Result(INFO, "bandwidth",
                      "not measured yet; run `racecast speedtest` "
                      "(or the Control Center's Speed test button)")
    dl = record.get("download_mbps", 0.0)
    ul = record.get("upload_mbps", 0.0)
    age = max(0.0, (now - record.get("ts", now)) / 86_400.0)
    where = _summary(record, age)
    if age > max_age_days:
        return Result(WARN, "bandwidth",
                      f"{where}, stale (older than {int(max_age_days)} d); "
                      "re-measure before the event")
    if dl < MIN_DOWN_MBPS or ul < MIN_UP_MBPS:
        return Result(WARN, "bandwidth",
                      f"{where}, below the {MIN_DOWN_MBPS:.0f}/{MIN_UP_MBPS:.0f} "
                      "Mbps minimum")
    if dl < REC_DOWN_MBPS or ul < REC_UP_MBPS:
        return Result(WARN, "bandwidth",
                      f"{where}, meets the minimum but is below the "
                      f"{REC_DOWN_MBPS:.0f}/{REC_UP_MBPS:.0f} Mbps recommended")
    return Result(PASS, "bandwidth",
                  f"{where}, meets the recommended "
                  f"{REC_DOWN_MBPS:.0f}/{REC_UP_MBPS:.0f} Mbps")


def default_runtime_dir(here):
    """Match the relay/get-cookies helper: repo layout (src/scripts/) ->
    <repo>/runtime ; distributed package (scripts/) -> here. Only used for a
    standalone `python3 speedtest.py` run; the CLI passes --runtime-dir."""
    if os.path.basename(here) == "scripts" and \
            os.path.basename(os.path.dirname(here)) == "src":
        return os.path.join(os.path.dirname(os.path.dirname(here)), "runtime")
    return here


def run(now, runtime_dir, runner=subprocess.run, which=shutil.which):
    """Run one measurement, append it to the history, and return the record.
    Raises SpeedtestUnavailable (binary missing) or SpeedtestFailed (run error)."""
    binary = find_binary(runtime_dir, which)
    if binary is None:
        raise SpeedtestUnavailable(
            "Ookla speedtest CLI not found. Run `racecast install-tools` "
            "(or install it manually).")
    proc = runner(run_argv(binary), capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        err = (proc.stderr or "").strip() or "no output"
        raise SpeedtestFailed(f"speedtest did not complete (no internet?): {err}")
    record = parse_result(out, now)
    append_record(record, runtime_dir)
    return record


def render(record, now):
    """Human-readable summary for the CLI."""
    c = classify(record, now)
    lines = ["Bandwidth speed test (Ookla)",
             f"  Download  {record['download_mbps']:.1f} Mbps   "
             f"(min {MIN_DOWN_MBPS:.0f} / rec {REC_DOWN_MBPS:.0f})",
             f"  Upload    {record['upload_mbps']:.1f} Mbps   "
             f"(min {MIN_UP_MBPS:.0f} / rec {REC_UP_MBPS:.0f})"]
    if record.get("ping_ms") is not None:
        extra = f"  Ping      {record['ping_ms']:.0f} ms"
        if record.get("jitter_ms") is not None:
            extra += f" · jitter {record['jitter_ms']:.0f} ms"
        if record.get("packet_loss") is not None:
            extra += f" · loss {record['packet_loss']:.0f}%"
        lines.append(extra)
    if record.get("server"):
        lines.append(f"  Server    {record['server']}")
    if record.get("isp"):
        lines.append(f"  ISP       {record['isp']}")
    if record.get("result_url"):
        lines.append(f"  Result    {record['result_url']}")
    lines.append(f"  => {c.level}: {c.detail}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="speedtest",
        description="Opt-in Ookla bandwidth speed test; logs the result locally.")
    ap.add_argument("--runtime-dir", default=None,
                    help="Directory holding speedtest-history.jsonl "
                         "(default: the project runtime dir).")
    ap.add_argument("--json", action="store_true",
                    help="Print the stored record as JSON instead of a summary.")
    a = ap.parse_args(argv)
    runtime_dir = a.runtime_dir or default_runtime_dir(
        os.path.dirname(os.path.abspath(__file__)))
    print("Running Ookla speed test; this takes 20-30 s.")
    try:
        rec = run(time.time(), runtime_dir)
    except SpeedtestUnavailable as exc:
        print(str(exc))
        return 2
    except SpeedtestFailed as exc:
        print(str(exc))
        return 1
    print(json.dumps(rec, indent=2) if a.json else render(rec, time.time()))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

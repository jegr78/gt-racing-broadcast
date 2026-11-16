#!/usr/bin/env python3
"""Load test for the driver telemetry probe ingest (#1110).

Maintainer tool. It runs N simulated senders through the sender script's own upload
path: each one encrypts the packets of a `.gt7rec` recording (or synthetic packets)
as format A at 60 Hz and posts them at the rate the relay sets. Run it against the
Funnel URL to measure what Funnel carries; pass the tailnet status URL to compare
with what the relay counted.

    python3 tools/gt7-probe-load.py --url https://<host>/console/probe/ingest --key KEY
    python3 tools/gt7-probe-load.py --url URL --key KEY --senders 15 --duration 120 \\
        --recording REC --status http://<tailnet-ip>:8088/telemetry/probe
"""
import argparse
import importlib.util
import json
import os
import statistics
import struct
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_crypto
import http_util
import logsetup

HZ = 60.0


def _sender_module():
    spec = importlib.util.spec_from_file_location(
        "racecast_sender", os.path.join(ROOT, "src", "relay", "racecast-sender.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def plains(recording=None, count=600):
    """Decrypted packets to replay: a recording's, or synthetic ones."""
    if recording:
        import gt7_recording
        return [plain for _ts, _kind, plain in gt7_recording.Recording(recording).packets()]
    out = []
    for i in range(count):
        body = bytearray(gt7_crypto.PACKET_LEN["A"])
        struct.pack_into("<I", body, 0, gt7_crypto.MAGIC)
        struct.pack_into("<f", body, 0x4C, 40.0)
        struct.pack_into("<h", body, 0x74, 1 + i // 600)
        out.append(bytes(body))
    return out


def run_sender(mod, url, key, name, packets, duration, stop, offset):
    up = mod.Uploader(url, key, name, say=lambda line: print(f"[{name}] {line}", flush=True))
    poster = threading.Thread(target=up.run, args=(stop,), daemon=True)
    poster.start()
    start = time.monotonic()
    pid = offset
    i = 0
    while not stop.is_set() and time.monotonic() - start < duration:
        plain = bytearray(packets[i % len(packets)])
        pid += 1
        struct.pack_into("<i", plain, 0x70, pid)
        up.put(gt7_crypto.encrypt(bytes(plain), "A", iv=pid))
        i += 1
        wait = start + i / HZ - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    deadline = time.monotonic() + 5
    while up.pending() and time.monotonic() < deadline:
        time.sleep(0.05)
    return up, i


def summary(results, seconds):
    lat = sorted(x for up, _n in results for x in up.latencies)
    sent = sum(n for _up, n in results)
    posted = sum(up.stats["posted"] for up, _n in results)
    failed = sum(up.stats["post_failed"] for up, _n in results)
    dropped = sum(up.stats["dropped"] for up, _n in results)

    def pct(p):
        return lat[min(len(lat) - 1, int(p * len(lat)))] * 1000 if lat else float("nan")
    return {"senders": len(results), "seconds": round(seconds, 1), "packets_sent": sent,
            "generated_hz_per_sender": round(sent / seconds / len(results), 1)
            if seconds and results else None,
            "posts": posted, "post_failed": failed, "dropped": dropped,
            "posts_per_s": round(posted / seconds, 1) if seconds else None,
            "latency_ms": {"p50": round(pct(0.5), 1), "p95": round(pct(0.95), 1),
                           "max": round(lat[-1] * 1000, 1) if lat else None,
                           "mean": round(statistics.fmean(lat) * 1000, 1) if lat else None}}


def main(argv=None):
    logsetup.harden_stdio()
    ap = argparse.ArgumentParser(description="Run simulated senders against the probe ingest.")
    ap.add_argument("--url", required=True, help="probe ingest URL (the Funnel one for Funnel)")
    ap.add_argument("--key", required=True, help="probe key from GET /telemetry/probe")
    ap.add_argument("--senders", type=int, default=15, help="simulated senders")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds per sender")
    ap.add_argument("--recording", help=".gt7rec whose packets the senders replay")
    ap.add_argument("--status", help="tailnet /telemetry/probe URL to compare relay counts")
    ap.add_argument("--reset", action="store_true",
                    help="clear the probe's counters first (needs --status)")
    args = ap.parse_args(argv)
    if args.reset:
        if not args.status:
            ap.error("--reset needs --status")
        http_util.post_json(args.status.rstrip("/") + "/reset", {}, timeout=10)
    mod = _sender_module()
    packets = plains(args.recording)
    stop = threading.Event()
    results = [None] * args.senders

    def one(k):
        results[k] = run_sender(mod, args.url, args.key, f"load {k + 1:02d}", packets,
                                args.duration, stop, offset=k * 1_000_000)
    threads = [threading.Thread(target=one, args=(k,), daemon=True) for k in range(args.senders)]
    started = time.monotonic()
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join(10)
    stop.set()
    report = summary([r for r in results if r], time.monotonic() - started)
    if args.status:
        doc = http_util.get_json(args.status, timeout=10)
        senders = {k: v for k, v in (doc.get("senders") or {}).items() if k.startswith("load ")}
        report["relay"] = {"received": sum(v["packets"]["received"] for v in senders.values()),
                           "lost": sum(v["packets"]["lost"] for v in senders.values()),
                           "max_batch_gap_s": max((v["max_batch_gap_s"] for v in senders.values()),
                                                  default=None)}
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

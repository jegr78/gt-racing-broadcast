#!/usr/bin/env python3
"""Multi-feed SCENARIO probe (#505) — maintainer harness, NOT shipped, NOT run in CI.

Where multifeed-429-probe.py runs STATIC cells (fixed N, fixed quality), this runs a DYNAMIC
timeline that mirrors a real broadcast: named feed SLOTS (A/B/POV) that get activated,
quality-switched, and deactivated mid-run — on ONE continuous measurement — so it captures the
transitions AND any throttle "memory" across them.

The motivating scenario (driver-swap splitscreen + optional POV), default phases:
    1  A=full            180s   normal on-air, 1 feed @1080p
    2  A=robust,B=robust 300s   DRIVER SWAP: both feeds @720p for ~5 min (the critical case)
    3  B=full            180s   A off, B back to 1080p (single feed)
    4  B=full,POV=robust 300s   B @1080p + POV @720p (does 1080p+720p fit the window?)

It reuses the REAL relay resolve + streamlink builders (importlib-loads racecast-feeds.py) so a
quality switch re-resolves at that tier's format (full=1080p, robust=720p, emergency=480p) and
restarts streamlink — exactly as the relay does. Per phase it logs the active feeds, aggregate
throughput, and any 429 (with the feed + seconds-into-phase). Serves+logs only, Ctrl-C safe.

Usage:
    python3 tools/multifeed-scenario-probe.py --urls urls.txt --cookies yt-cookies.txt --out runs
    python3 tools/multifeed-scenario-probe.py --urls urls.txt --dry-run
    # custom timeline (feed=quality,… @seconds), repeatable, in order:
    python3 tools/multifeed-scenario-probe.py --urls urls.txt \
        --phase 'A=full@120' --phase 'A=robust,B=robust@300' --phase 'B=full@120'
Feed slots map to --urls lines in order: A=line1, B=line2, POV=line3.
"""
import argparse
import glob
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SLOTS = ("A", "B", "POV")

DEFAULT_PHASES = [
    ({"A": "full"}, 180),
    ({"A": "robust", "B": "robust"}, 300),
    ({"B": "full"}, 180),
    ({"B": "full", "POV": "robust"}, 300),
]

_THROTTLE_RE = re.compile(r"\b429\b|too many requests|sign in to confirm", re.IGNORECASE)


def is_throttle(line):
    """True if a streamlink/yt-dlp stderr line signals the per-IP 429 / bot-wall. Pure."""
    return bool(_THROTTLE_RE.search(line))


def parse_phase(spec):
    """'A=robust,B=robust@300' -> ({'A':'robust','B':'robust'}, 300.0). Pure."""
    body, _, dur = spec.partition("@")
    feeds = {}
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        slot, _, q = part.partition("=")
        slot = slot.strip().upper()
        q = (q.strip() or "full")
        if slot not in SLOTS:
            raise ValueError(f"unknown feed slot {slot!r} (use A/B/POV)")
        feeds[slot] = q
    return feeds, float(dur or 0)


def read_urls(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            s = raw.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out


def _load_relay():
    spec = importlib.util.spec_from_file_location(
        "iroscenariofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FeedWorker:
    """One persistent feed slot. start(quality)/stop()/switch(quality) mirror the relay:
    a quality switch re-resolves at the tier's format and restarts streamlink."""

    def __init__(self, slot, url, fe, cookies, out_dir, events, log):
        self.slot = slot
        self.url = url
        self.fe = fe
        self.cookies = cookies
        self.events = events           # shared list of (ts, slot, kind, detail)
        self.log = log
        self.logfile = open(  # noqa: SIM115 — long-lived, closed at stop()
            os.path.join(out_dir, f"feed_{slot}.log"), "a", encoding="utf-8")
        self.quality = None
        self.proc = None
        self.bytes_total = 0
        self.threw_429 = False
        self._stop = threading.Event()
        self._runner = None

    def _wlog(self, msg):
        line = f"{time.strftime('%H:%M:%S')} {self.slot} {msg}"
        self.logfile.write(line + "\n"); self.logfile.flush()

    def running(self):
        return self._runner is not None and self._runner.is_alive()

    def start(self, quality):
        if self.running():
            return
        self.quality = quality
        self._stop.clear()
        self.events.append((time.time(), self.slot, "start", quality))
        self._wlog(f"start quality={quality}")
        self._runner = threading.Thread(target=self._run, daemon=True)
        self._runner.start()

    def switch(self, quality):
        if self.quality == quality and self.running():
            return
        self._wlog(f"switch {self.quality}->{quality}")
        self.events.append((time.time(), self.slot, "switch", quality))
        self.stop()
        self.start(quality)

    def stop(self):
        if not self.running():
            return
        self._stop.set()
        self._terminate()
        self._runner.join(timeout=8)
        self.events.append((time.time(), self.slot, "stop", self.quality))
        self._wlog("stop")

    def _terminate(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except OSError:
                pass  # already gone

    def _resolve(self):
        if self.url.startswith("http") and "twitch.tv" in self.url:
            return self.url
        fmt = self.fe.quality_ytdlp_fmt(self.quality)
        cmd = self.fe.ytdlp_resolve_cmd(self.url, self.cookies, fmt=fmt)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._wlog(f"resolve-error {exc}"); return None
        if res.returncode != 0:
            first = (res.stderr or "").strip().splitlines()[:1]
            if first and is_throttle(first[0]):
                self._flag_429(f"resolve: {first[0][:80]}")
            self._wlog(f"resolve-fail {first}"); return None
        # stdout also carries the relay's "rcq …"/"rcs …" --print lines: take the URL.
        out = [ln for ln in (res.stdout or "").splitlines() if ln.startswith("http")]
        return out[0] if out else None

    def _flag_429(self, detail):
        if not self.threw_429:
            self.threw_429 = True
        self.events.append((time.time(), self.slot, "429", detail))
        self._wlog(f"[429] {detail}")

    def _run(self):
        platform = "twitch" if "twitch.tv" in self.url else "youtube"
        while not self._stop.is_set():
            target = self._resolve()
            if not target:
                self._stop.wait(15); continue
            cmd = self.fe.streamlink_fanout_cmd(target, platform=platform,
                                                cookies=self.cookies, tier=self.quality)
            self._wlog("streamlink: " + " ".join(cmd[:6]) + " …")
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, bufsize=0)
            except OSError as exc:
                self._wlog(f"spawn-error {exc}"); self._stop.wait(5); continue
            terr = threading.Thread(target=self._drain_err,
                                    args=(io.TextIOWrapper(self.proc.stderr, errors="replace"),),
                                    daemon=True)
            terr.start()
            while self.proc.poll() is None and not self._stop.is_set():
                chunk = self.proc.stdout.read(65536)
                if not chunk:
                    break
                self.bytes_total += len(chunk)
            if self._stop.is_set():
                self._terminate(); break
            self._wlog(f"streamlink exited rc={self.proc.poll()}")
            self._stop.wait(3)

    def _drain_err(self, stream):
        for raw in stream:
            line = raw.rstrip("\n")
            if is_throttle(line):
                self._flag_429(line[:100])
            self._wlog("[sl] " + line)


def run_scenario(args, urls, phases):
    fe = _load_relay()
    out_dir = os.path.join(args.out, args.scenario_id)
    os.makedirs(out_dir, exist_ok=True)

    def log(msg):
        print(msg, flush=True)

    url_by_slot = {SLOTS[i]: urls[i] for i in range(min(len(SLOTS), len(urls)))}
    events = []
    workers = {s: FeedWorker(s, url_by_slot[s], fe, args.cookies, out_dir, events, log)
               for s in url_by_slot}

    tl_path = os.path.join(out_dir, "timeline.jsonl")
    tl = open(tl_path, "w", encoding="utf-8")  # noqa: SIM115
    phase_recs = []
    t0 = time.time()

    def sample(bytes_before):
        return {s: workers[s].bytes_total - bytes_before.get(s, 0) for s in workers}

    try:
        for idx, (feeds, dur) in enumerate(phases, 1):
            # reconcile slots to this phase
            for s in SLOTS:
                w = workers.get(s)
                if w is None:
                    continue
                if s in feeds:
                    if not w.running():
                        w.start(feeds[s])
                    elif w.quality != feeds[s]:
                        w.switch(feeds[s])
                elif w.running():
                    w.stop()
            active = {s: feeds[s] for s in feeds if s in workers}
            p_start = time.time()
            bytes0 = {s: workers[s].bytes_total for s in workers}
            log(f"\n=== PHASE {idx}: {active}  {dur:.0f}s  (t+{p_start-t0:.0f}s) ===")
            # monitor the phase
            while time.time() - p_start < dur:
                time.sleep(5)
                d = sample(bytes0)
                mbps = {s: round(d[s] * 8 / 1e6 / max(1, time.time() - p_start), 2) for s in active}
                tl.write(json.dumps({"t": round(time.time() - t0, 1), "phase": idx,
                                     "active": active, "mbps": mbps,
                                     "n429": sum(1 for e in events if e[2] == "429")}) + "\n")
                tl.flush()
            # phase summary
            dur_real = time.time() - p_start
            p429 = [e for e in events if e[2] == "429" and p_start <= e[0] <= time.time()]
            agg = sum(workers[s].bytes_total - bytes0[s] for s in active) * 8 / 1e6 / max(1, dur_real)
            first429 = (min(e[0] for e in p429) - p_start) if p429 else None
            rec = {"phase": idx, "feeds": active, "duration_s": round(dur_real),
                   "agg_mbps": round(agg, 2), "threw_429": bool(p429),
                   "first_429_into_phase_s": round(first429, 1) if first429 is not None else None,
                   "n_429_events": len(p429)}
            phase_recs.append(rec)
            log(f"    -> agg={rec['agg_mbps']}Mbps  429={'YES @%.0fs' % first429 if p429 else 'no'}")
    except KeyboardInterrupt:
        log("Ctrl-C — stopping")
    finally:
        for w in workers.values():
            w.stop()
        tl.close()

    summary = {"scenario_id": args.scenario_id, "phases": phase_recs,
               "urls": {s: url_by_slot[s] for s in url_by_slot}}
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    log("\n===== SCENARIO SUMMARY =====")
    log(f"{'phase':6} {'feeds':32} {'agg Mbps':9} {'429?':16}")
    for r in phase_recs:
        f = ",".join(f"{k}:{v}" for k, v in r["feeds"].items())
        t = f"YES @{r['first_429_into_phase_s']}s" if r["threw_429"] else "no"
        log(f"{r['phase']:<6} {f:32} {r['agg_mbps']:<9} {t:16}")
    log(f"\n-> {os.path.join(out_dir, 'summary.json')}")
    return summary


def do_dry_run(urls, phases):
    print("SCENARIO dry-run — feed slots:", {SLOTS[i]: urls[i] for i in range(min(len(SLOTS), len(urls)))})
    t = 0
    for idx, (feeds, dur) in enumerate(phases, 1):
        print(f"  phase {idx}: t+{t:>4.0f}s  {feeds}  for {dur:.0f}s")
        t += dur
    print(f"  total ~{t:.0f}s")


def do_summarize(paths):
    for pat in paths:
        for path in glob.glob(pat):
            with open(path, encoding="utf-8") as fh:
                s = json.load(fh)
            print(f"# {s.get('scenario_id')}")
            for r in s.get("phases", []):
                f = ",".join(f"{k}:{v}" for k, v in r["feeds"].items())
                t = f"YES @{r['first_429_into_phase_s']}s" if r["threw_429"] else "no"
                print(f"  phase {r['phase']}: {f:32} agg={r['agg_mbps']}Mbps  429={t}")


def build_parser():
    p = argparse.ArgumentParser(description="Multi-feed scenario probe (#505) — off-event only.")
    p.add_argument("--urls", help="file: distinct live URLs, one per line (A,B,POV = first 3)")
    p.add_argument("--cookies", default=None)
    p.add_argument("--phase", action="append", default=[],
                   help="'A=full,B=robust@300' (repeatable, in order); default = the driver-swap scenario")
    p.add_argument("--out", default="scenario-runs")
    p.add_argument("--scenario-id", default="driver-swap")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--summarize", nargs="+", metavar="summary.json")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.summarize:
        do_summarize(args.summarize); return 0
    phases = [parse_phase(s) for s in args.phase] if args.phase else DEFAULT_PHASES
    urls = read_urls(args.urls) if args.urls else []
    if len(urls) < 2:
        print("ERROR: need at least 2 distinct URLs (A,B); 3 for POV", file=sys.stderr); return 2
    if args.dry_run:
        do_dry_run(urls, phases); return 0
    run_scenario(args, urls, phases)
    return 0


if __name__ == "__main__":
    sys.exit(main())

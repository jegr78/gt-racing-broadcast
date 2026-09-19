#!/usr/bin/env python3
"""Multi-feed 429 probe (#505) — maintainer harness, NOT shipped, NOT run in CI.

Measures how many DISTINCT sustained googlevideo (YouTube) / Twitch pulls a given IP
tolerates before a 429, how long until it hits, and how it recovers — the empirical basis
for the #489 hardening. It reuses the REAL relay resolve + streamlink flags (importlib-loads
`src/relay/racecast-feeds.py`, exactly like `tools/fanout-soak.py`) so the measured behaviour
matches production, not a synthetic approximation.

It SERVES + LOGS only: it starts N `streamlink --stdout` pulls of N distinct live URLs, drains
their bytes (continuously, no backpressure — same as the relay fan-out ring), scans streamlink
stderr for 429/throttle markers, and writes per-pull logs + a machine-readable `results.jsonl`.
It provokes nothing beyond the pulls themselves and is safe to Ctrl-C.

HARD CONSTRAINT — OFF-EVENT ONLY. Deliberately provoking 429s against the googlevideo IP a live
broadcast pulls from PROLONGS the very outage we are studying. This refuses to run while a relay
is active (guard below) unless --force, and the on-box runbook stops at the first sign of event
collision. See docs/superpowers/specs/2026-07-14-multifeed-429-viability-design.md.

Usage (one cell at a time; see the runbook for the full matrix + cool-downs):
    # dry-run: print the resolve/pull commands + activation schedule, spawn nothing
    python3 tools/multifeed-429-probe.py --urls urls-yt.txt --n 2 --dry-run

    # a real cell — YouTube, 2 distinct pullers, burst start, 20-min survival window
    python3 tools/multifeed-429-probe.py --urls urls-yt.txt --n 2 \
        --activation burst --duration-s 1200 --cell-id yt-2-burst --out probe-runs

    # the staggered + gentler-cadence arm
    python3 tools/multifeed-429-probe.py --urls urls-yt.txt --n 2 \
        --activation staggered --stagger-s 5 --hls-live-edge 6 --cell-id yt-2-stagger --out probe-runs

    # Twitch arm (account-less; the Twitch plugin resolves in-process)
    python3 tools/multifeed-429-probe.py --urls urls-tw.txt --platform twitch --n 2 \
        --cell-id tw-2-burst --out probe-runs

    # the recovery arm — provoke a 429 then observe clear time under fixed vs backoff retry
    python3 tools/multifeed-429-probe.py --urls urls-yt.txt --n 2 --retry-mode backoff \
        --cell-id yt-2-recovery-backoff --out probe-runs

    # after the run: fold every cell's results.jsonl into the matrix table
    python3 tools/multifeed-429-probe.py --summarize probe-runs/*/results.jsonl
"""
import argparse
import glob
import importlib.util
import io
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# --------------------------------------------------------------------------------------
# Pure helpers (no relay import — unit-tested in tests/test_multifeed_probe.py)
# --------------------------------------------------------------------------------------

def activation_delays(n, mode, stagger_s, jitter_s, rng):
    """Per-pull start delay (seconds) for N pulls. Pure (rng injected for determinism).

    - burst: every pull starts at 0 (the current relay behaviour — all at once).
    - staggered: pull i starts at i*stagger_s, each (i>0) nudged by +/- jitter_s, clamped >=0.
    """
    if mode == "burst":
        return [0.0] * n
    out = []
    for i in range(n):
        base = i * stagger_s
        jit = rng.uniform(-jitter_s, jitter_s) if i > 0 and jitter_s > 0 else 0.0
        out.append(max(0.0, base + jit))
    return out


def backoff_delay(attempt, mode, base_s, cap_s, jitter_s, rng):
    """Seconds to wait before re-launching a pull that exited (throttle/EOF). Pure.

    - fixed: always `base_s` (the current ~15 s retry storm — RESOLVE_RETRY).
    - backoff: exponential base_s * 2**(attempt-1), capped at cap_s, plus [0, jitter_s) jitter.
      `attempt` is 1-based (the 1st re-launch after the initial start is attempt=1).
    """
    if mode == "fixed":
        return float(base_s)
    exp = base_s * (2 ** max(0, attempt - 1))
    capped = min(cap_s, exp)
    return float(capped) + (rng.uniform(0, jitter_s) if jitter_s > 0 else 0.0)


_THROTTLE_RE = re.compile(r"\b429\b|too many requests", re.IGNORECASE)
_403_RE = re.compile(r"\b403\b|forbidden", re.IGNORECASE)
_RELOAD_RE = re.compile(
    r"failed to reload|unable to open url|read timeout|got error|"
    r"could not|connection reset|no data|stream ended",
    re.IGNORECASE,
)


def classify_pull_line(line):
    """Classify one streamlink stderr line. Pure. Returns one of:
    'throttle_429' (the per-IP throttle we study), 'http_403', 'reload_error', or None.
    429 is checked first (most specific / the signal of record)."""
    if _THROTTLE_RE.search(line):
        return "throttle_429"
    if _403_RE.search(line):
        return "http_403"
    if _RELOAD_RE.search(line):
        return "reload_error"
    return None


def relay_running(pid_path, is_alive):
    """True if a relay PID file exists and its PID is alive (is_alive injected). Pure-ish.
    The off-event guard: a live relay means the box may be pulling for a broadcast."""
    try:
        with open(pid_path, encoding="utf-8") as fh:
            pid = int((fh.read() or "0").strip() or "0")
    except (OSError, ValueError):
        return False
    return pid > 0 and bool(is_alive(pid))


def _fmt_secs(v):
    return "—" if v is None else f"{v:.0f}"


def summarize_cells(records):
    """Fold aggregate cell records (kind == 'cell') into markdown matrix rows. Pure.
    Returns (header_line, sep_line, [row_line, ...])."""
    header = ("| cell | platform | N | activation | retry | 429? | "
              "t→first-429 (s) | sustained (s) | recovery (s) | agg Mbps |")
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    rows = []
    for r in sorted((x for x in records if x.get("kind") == "cell"),
                    key=lambda x: x.get("cell_id", "")):
        rows.append(
            "| {cell} | {plat} | {n} | {act} | {retry} | {t429} | {tfirst} | "
            "{sust} | {rec} | {mbps} |".format(
                cell=r.get("cell_id", "?"),
                plat=r.get("platform", "?"),
                n=r.get("n", "?"),
                act=r.get("activation", "?"),
                retry=r.get("retry_mode", "?"),
                t429="yes" if r.get("threw_429") else "no",
                tfirst=_fmt_secs(r.get("time_to_first_429_s")),
                sust=_fmt_secs(r.get("sustained_s")),
                rec=_fmt_secs(r.get("recovery_s")),
                mbps=("—" if r.get("agg_mbps") is None else f"{r['agg_mbps']:.2f}"),
            )
        )
    return header, sep, rows


def read_url_list(path):
    """Read a URL-list file: one URL per line, '#' comments and blanks skipped. Pure."""
    urls = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def apply_live_edge(cmd, edge):
    """Return a copy of a streamlink argv with --hls-live-edge overridden to `edge`
    (or appended if absent). Pure. The staggered arm raises the live edge for a gentler
    reload cadence without forking the relay's flag builder."""
    if edge is None:
        return list(cmd)
    out = list(cmd)
    if "--hls-live-edge" in out:
        i = out.index("--hls-live-edge")
        if i + 1 < len(out):
            out[i + 1] = str(edge)
        return out
    # append before the trailing `-- <url> <selector>` if present, else at the end
    if "--" in out:
        i = out.index("--")
        return out[:i] + ["--hls-live-edge", str(edge)] + out[i:]
    return out + ["--hls-live-edge", str(edge)]


# --------------------------------------------------------------------------------------
# Relay module (lazy — only the run path needs it, so tests import this file cheaply)
# --------------------------------------------------------------------------------------

def _load_relay():
    path = os.path.join(ROOT, "src", "relay", "racecast-feeds.py")
    spec = importlib.util.spec_from_file_location("iro505feeds", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------------------
# One pull worker
# --------------------------------------------------------------------------------------

class Pull:
    """One streamlink pull of one distinct live URL. Owns its own subprocess lifecycle,
    re-launching on exit per the retry policy, and records timestamps for the results."""

    def __init__(self, idx, url, fe, args, out_dir, log, stop_evt, rng):
        self.idx = idx
        self.url = url
        self.fe = fe
        self.args = args
        self.log = log
        self.stop = stop_evt
        self.rng = rng
        self.logfile = open(  # noqa: SIM115 — long-lived handle owned by this Pull, closed at teardown
            os.path.join(out_dir, f"pull_{idx}.log"), "a", encoding="utf-8")
        self.proc = None
        # results
        self.t_start = None
        self.t_first_bytes = None
        self.t_first_429 = None
        self.t_recovered = None
        self.bytes_total = 0
        self.attempts = 0
        self.n_429_lines = 0
        self.threw_429 = False

    def _wlog(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} pull{self.idx} {msg}"
        self.logfile.write(line + "\n")
        self.logfile.flush()
        self.log(line)

    def _resolve_target(self):
        """YouTube: yt-dlp -g to an HLS URL (with the box PATH deno present). Twitch: the
        channel URL itself (the Twitch plugin resolves in-process). Returns (target, ok)."""
        if self.args.platform == "twitch":
            return self.url, True
        # tier drives the RESOLVE format too (robust=720p, emergency=480p) — mirrors the
        # relay, so --quality robust actually pulls a ~3 Mbps 720p rendition, not 1080p.
        fmt = self.fe.quality_ytdlp_fmt(self.args.quality)
        cmd = self.fe.ytdlp_resolve_cmd(self.url, self.args.cookies, fmt=fmt)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._wlog(f"resolve-error {exc}")
            return None, False
        if res.returncode != 0:
            first = (res.stderr or "").strip().splitlines()[:1]
            self._wlog(f"resolve-fail rc={res.returncode} {first}")
            return None, False
        # stdout also carries the relay's "rcq …" --print line: take the URL.
        hls = [ln for ln in (res.stdout or "").splitlines() if ln.startswith("http")]
        return (hls[0], True) if hls else (None, False)

    def _streamlink_cmd(self, target):
        cmd = self.fe.streamlink_fanout_cmd(
            target, platform=self.args.platform,
            cookies=self.args.cookies, tier=self.args.quality,
        )
        return apply_live_edge(cmd, self.args.hls_live_edge)

    def _drain_stdout(self, proc):
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            self.bytes_total += len(chunk)
            if self.t_first_bytes is None:
                self.t_first_bytes = time.time()
            if self.t_first_429 is not None and self.t_recovered is None:
                self.t_recovered = time.time()
                self._wlog(f"recovered after {self.t_recovered - self.t_first_429:.0f}s")

    def _drain_stderr(self, proc):
        for raw in proc.stderr:
            line = raw.rstrip("\n")
            kind = classify_pull_line(line)
            tag = f"[{kind}] " if kind else ""
            self._wlog(f"[streamlink] {tag}{line}")
            if kind == "throttle_429":
                self.n_429_lines += 1
                if self.t_first_429 is None:
                    self.t_first_429 = time.time()
                    self.threw_429 = True
                    elapsed = self.t_first_429 - (self.t_first_bytes or self.t_start)
                    self._wlog(f"FIRST 429 at +{elapsed:.0f}s")

    def run(self, deadline):
        self.t_start = time.time()
        while not self.stop.is_set() and time.time() < deadline:
            self.attempts += 1
            target, ok = self._resolve_target()
            if not ok:
                delay = backoff_delay(self.attempts, self.args.retry_mode,
                                      self.args.retry_base_s, self.args.backoff_cap_s,
                                      self.args.backoff_jitter_s, self.rng)
                self._wlog(f"retry in {delay:.0f}s (resolve failed)")
                self.stop.wait(delay)
                continue
            cmd = self._streamlink_cmd(target)
            self._wlog(f"launch (attempt {self.attempts}): {' '.join(cmd)}")
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, text=False,
                )
            except OSError as exc:
                self._wlog(f"spawn-error {exc}")
                self.stop.wait(self.args.retry_base_s)
                continue
            # stderr is text; reopen as text wrapper
            err = io_text(self.proc.stderr)
            t_out = threading.Thread(target=self._drain_stdout, args=(self.proc,), daemon=True)
            t_err = threading.Thread(target=self._drain_stderr, args=(_Proc(err),), daemon=True)
            t_out.start()
            t_err.start()
            # wait for exit or global stop
            while self.proc.poll() is None and not self.stop.is_set() and time.time() < deadline:
                time.sleep(0.5)
            if self.stop.is_set() or time.time() >= deadline:
                self._terminate()
                break
            rc = self.proc.poll()
            self._wlog(f"streamlink exited rc={rc}")
            delay = backoff_delay(self.attempts, self.args.retry_mode,
                                  self.args.retry_base_s, self.args.backoff_cap_s,
                                  self.args.backoff_jitter_s, self.rng)
            self._wlog(f"re-launch in {delay:.0f}s")
            self.stop.wait(delay)
        self._terminate()
        self.logfile.flush()

    def _terminate(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except OSError:
                pass  # process already gone / pipe closed — nothing to reap

    def record(self, wall_s):
        sustained = None
        if self.t_first_429 is not None:
            base = self.t_first_bytes or self.t_start
            sustained = self.t_first_429 - base
        elif self.t_first_bytes is not None:
            sustained = time.time() - self.t_first_bytes
        recovery = None
        if self.t_first_429 is not None and self.t_recovered is not None:
            recovery = self.t_recovered - self.t_first_429
        mbps = (self.bytes_total * 8 / 1e6 / wall_s) if wall_s > 0 else 0.0
        return {
            "kind": "pull",
            "idx": self.idx,
            "url": self.url,
            "attempts": self.attempts,
            "threw_429": self.threw_429,
            "n_429_lines": self.n_429_lines,
            "time_to_first_429_s": (
                None if self.t_first_429 is None
                else self.t_first_429 - (self.t_first_bytes or self.t_start)
            ),
            "sustained_s": sustained,
            "recovery_s": recovery,
            "bytes": self.bytes_total,
            "mbps": mbps,
        }


class _Proc:
    """Adapter so _drain_stderr can iterate a text stream we opened separately."""
    def __init__(self, stderr):
        self.stderr = stderr


def io_text(binary_stream):
    return io.TextIOWrapper(binary_stream, encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------------------
# Run one cell
# --------------------------------------------------------------------------------------

def run_cell(args, urls):
    fe = _load_relay()
    out_dir = os.path.join(args.out, args.cell_id)
    os.makedirs(out_dir, exist_ok=True)

    def log(msg):
        print(msg, flush=True)

    rng = random.Random(args.seed)
    delays = activation_delays(len(urls), args.activation, args.stagger_s, args.jitter_s, rng)
    log(f"# cell {args.cell_id}: {len(urls)} pull(s) platform={args.platform} "
        f"activation={args.activation} retry={args.retry_mode} "
        f"duration={args.duration_s}s hls-live-edge={args.hls_live_edge or 'relay-default'}")
    for i, (u, d) in enumerate(zip(urls, delays, strict=True)):
        log(f"#  pull {i}: +{d:.0f}s  {u}")

    stop_evt = threading.Event()
    pulls = [Pull(i, u, fe, args, out_dir, log, stop_evt, random.Random(args.seed + i))
             for i, u in enumerate(urls)]

    def on_sigint(_sig, _frm):
        log("# Ctrl-C — stopping all pulls…")
        stop_evt.set()
    signal.signal(signal.SIGINT, on_sigint)

    t0 = time.time()
    deadline = t0 + args.duration_s
    threads = []
    for i, p in enumerate(pulls):
        d = delays[i]
        t = threading.Thread(target=_delayed_run, args=(p, deadline, d, stop_evt), daemon=True)
        t.start()
        threads.append(t)
    # wait until deadline or stop
    while time.time() < deadline and not stop_evt.is_set():
        time.sleep(1)
    stop_evt.set()
    for t in threads:
        t.join(timeout=15)

    wall = time.time() - t0
    pull_recs = [p.record(wall) for p in pulls]
    threw = any(r["threw_429"] for r in pull_recs)
    t_firsts = [r["time_to_first_429_s"] for r in pull_recs if r["time_to_first_429_s"] is not None]
    sustaineds = [r["sustained_s"] for r in pull_recs if r["sustained_s"] is not None]
    recoveries = [r["recovery_s"] for r in pull_recs if r["recovery_s"] is not None]
    agg_mbps = sum(r["mbps"] for r in pull_recs)
    cell_rec = {
        "kind": "cell",
        "cell_id": args.cell_id,
        "platform": args.platform,
        "n": len(urls),
        "activation": args.activation,
        "retry_mode": args.retry_mode,
        "hls_live_edge": args.hls_live_edge,
        "duration_s": args.duration_s,
        "wall_s": wall,
        "threw_429": threw,
        "time_to_first_429_s": min(t_firsts) if t_firsts else None,
        "sustained_s": min(sustaineds) if sustaineds else None,
        "recovery_s": (sum(recoveries) / len(recoveries)) if recoveries else None,
        "agg_mbps": agg_mbps,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
    }
    results_path = os.path.join(out_dir, "results.jsonl")
    with open(results_path, "w", encoding="utf-8") as fh:
        for r in pull_recs:
            fh.write(json.dumps(r) + "\n")
        fh.write(json.dumps(cell_rec) + "\n")
    log(f"# done: 429={'YES' if threw else 'no'} "
        f"t→first-429={_fmt_secs(cell_rec['time_to_first_429_s'])}s "
        f"sustained={_fmt_secs(cell_rec['sustained_s'])}s "
        f"recovery={_fmt_secs(cell_rec['recovery_s'])}s "
        f"agg={agg_mbps:.2f} Mbps -> {results_path}")
    return cell_rec


def _delayed_run(pull, deadline, delay, stop_evt):
    if delay > 0:
        stop_evt.wait(delay)
    if not stop_evt.is_set():
        pull.run(deadline)


# --------------------------------------------------------------------------------------
# Dry-run + summarize
# --------------------------------------------------------------------------------------

def do_dry_run(args, urls):
    fe = _load_relay()
    rng = random.Random(args.seed)
    delays = activation_delays(len(urls), args.activation, args.stagger_s, args.jitter_s, rng)
    print(f"# DRY-RUN cell {args.cell_id}: {len(urls)} pull(s) platform={args.platform} "
          f"activation={args.activation} retry={args.retry_mode} duration={args.duration_s}s")
    for i, (u, d) in enumerate(zip(urls, delays, strict=True)):
        if args.platform == "twitch":
            target = u
            resolve = "(twitch plugin resolves in-process — no yt-dlp hop)"
        else:
            target = "<HLS_URL from yt-dlp -g>"
            resolve = " ".join(fe.ytdlp_resolve_cmd(u, args.cookies))
        cmd = apply_live_edge(
            fe.streamlink_fanout_cmd(target, platform=args.platform,
                                     cookies=args.cookies, tier=args.quality),
            args.hls_live_edge)
        print(f"\n## pull {i}  start +{d:.0f}s  url={u}")
        print(f"   resolve : {resolve}")
        print(f"   pull    : {' '.join(cmd)}")
    print("\n# (dry-run: nothing spawned)")


def do_summarize(paths):
    records = []
    for pat in paths:
        for path in glob.glob(pat):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    header, sep, rows = summarize_cells(records)
    print(header)
    print(sep)
    for r in rows:
        print(r)
    if not rows:
        print("| (no cell records found) |")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="Multi-feed 429 probe (#505) — off-event only.")
    p.add_argument("--urls", help="file: one distinct live URL per line (# comments ok)")
    p.add_argument("--url", action="append", default=[], help="a distinct live URL (repeatable)")
    p.add_argument("--platform", choices=("youtube", "twitch"), default="youtube")
    p.add_argument("--n", type=int, default=0, help="use the first N urls (default: all)")
    p.add_argument("--activation", choices=("burst", "staggered"), default="burst")
    p.add_argument("--stagger-s", type=float, default=5.0)
    p.add_argument("--jitter-s", type=float, default=2.0)
    p.add_argument("--hls-live-edge", type=int, default=None,
                   help="override streamlink --hls-live-edge (staggered arm: gentler cadence)")
    p.add_argument("--quality", choices=("full", "robust", "emergency"), default="full")
    p.add_argument("--cookies", default=None, help="pull-cookie jar (NEVER the RTMP/output identity)")
    p.add_argument("--duration-s", type=int, default=1200, help="survival window (default 20 min)")
    p.add_argument("--retry-mode", choices=("fixed", "backoff"), default="fixed")
    p.add_argument("--retry-base-s", type=float, default=15.0, help="fixed retry / backoff base")
    p.add_argument("--backoff-cap-s", type=float, default=120.0)
    p.add_argument("--backoff-jitter-s", type=float, default=5.0)
    p.add_argument("--cooldown-s", type=int, default=600,
                   help="informational only; the runbook enforces cool-down between cells")
    p.add_argument("--cell-id", default="cell", help="label for the results record + out subdir")
    p.add_argument("--out", default="probe-runs", help="output dir (per-cell subdir created)")
    p.add_argument("--seed", type=int, default=1, help="RNG seed (jitter/backoff reproducibility)")
    p.add_argument("--relay-pid", default=os.path.join(ROOT, "runtime", "relay.pid"),
                   help="relay PID file for the off-event guard")
    p.add_argument("--force", action="store_true", help="run even if a relay is active (DANGER)")
    p.add_argument("--dry-run", action="store_true", help="print commands + schedule, spawn nothing")
    p.add_argument("--summarize", nargs="+", metavar="RESULTS.JSONL",
                   help="fold cell results into the matrix table and exit")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.summarize:
        do_summarize(args.summarize)
        return 0

    urls = list(args.url)
    if args.urls:
        urls = read_url_list(args.urls) + urls
    if args.n and args.n > 0:
        urls = urls[:args.n]
    if not urls:
        print("ERROR: no URLs (use --urls FILE or --url ...)", file=sys.stderr)
        return 2

    if args.dry_run:
        do_dry_run(args, urls)
        return 0

    # off-event guard
    if relay_running(args.relay_pid, _pid_alive) and not args.force:
        print("REFUSING: a relay appears to be running (off-event guard). This probe "
              "provokes 429s and would prolong a live outage.\n"
              "  - Confirm no event is scheduled and stop the relay: racecast relay stop\n"
              "  - Then re-run, or pass --force if you are certain the box is idle.",
              file=sys.stderr)
        return 3

    run_cell(args, urls)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Relay restart soak (#619). Maintainer diagnostic, NOT shipped, NOT run in CI.

Answers the one question in #619 that needs hours rather than a window: **does every
restart heal itself, or does something accumulate?** The producer's report is a picture
that drifts further behind across an event night. A restart was shown to park OBS deep
in the ring (#614); since #629/#630 the relay rejoins OBS after one. That the first
restart recovers proves nothing about the twentieth.

It drives a RUNNING relay over its control port and never touches OBS, so it can run
from another machine over the tailnet while OBS keeps rendering on the producer host.
That also means it survives a dropped ssh session, unlike a relay started over one.

    python3 tools/relay-restart-soak.py --relay http://100.x.y.z:8088 --hours 2

What it does, every cycle: sample `/status` every `--sample-every` seconds, and every
`--every` minutes trigger `GET /reload/<feed>` on the on-air feed. That is the
director's restart in place, which is a restart with a consumer still attached, which
is exactly the case `should_obs_reconnect` covers.

**Space YouTube restarts out.** Reloading the same YouTube URL back to back is an
immediate 429 from the same IP, and a throttle outlasts the soak. The default cadence
is deliberately slow; use a Twitch source when you want restarts close together.

Samples and the analysis go to a JSONL next to the script's --out. The verdict comes
from `tools/restart_soak.py`, which is pure and unit-tested in
`tests/test_restart_soak.py`; every threshold there is a quantity the relay already
defines (the reserve from /status, the deadline from the heartbeat).

Exit code: 0 on PASS, 1 on FAIL, 2 on UNKNOWN or a setup error.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import restart_soak as analysis   # noqa: E402
from restart_soak import relay_url, render, sample_of   # noqa: E402

UA = {"User-Agent": "racecast-restart-soak/1.0"}


def get_json(base, path, timeout=8.0):
    """GET one relay endpoint. Returns (payload, None) or (None, reason). Never raises:
    a soak must survive a blip and say so in the sample, not die three hours in."""
    try:
        req = urllib.request.Request(base + path, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (scheme checked)
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def run(base, hours, every_min, sample_every_s, out_path, feed=None,
        clock=time.monotonic, sleep=time.sleep, progress=print):
    status, why = get_json(base, "/status")
    if status is None:
        progress(f"relay not reachable at {base}: {why}")
        return 2
    reserve = status.get("feed_prebuffer_s")
    if not isinstance(reserve, (int, float)) or isinstance(reserve, bool):
        progress("relay does not report feed_prebuffer_s, so it is too old for this soak")
        return 2
    target = feed or ((status.get("live") or {}).get("feed"))
    if not target:
        progress("no feed is on air; arm one first")
        return 2
    progress(f"soaking {base} feed {target} for {hours} h, a restart every "
             f"{every_min} min, reserve {reserve} s")

    t0 = clock()
    deadline = t0 + hours * 3600.0
    next_restart = t0 + every_min * 60.0
    samples, restarts = [], []
    with open(out_path, "w", encoding="utf-8") as fh:
        while clock() < deadline:
            now = clock()
            st, why = get_json(base, "/status")
            row = sample_of(st, now - t0) if st else {"t": round(now - t0, 1),
                                                      "error": why}
            samples.append(row)
            fh.write(json.dumps(row) + "\n"); fh.flush()
            if now >= next_restart:
                _res, rwhy = get_json(base, f"/reload/{target}")
                restarts.append(now - t0)
                progress(f"  t+{now - t0:6.0f}s  restart {len(restarts)}"
                         + (f" (failed: {rwhy})" if rwhy else ""))
                next_restart = now + every_min * 60.0
            sleep(sample_every_s)

    summary = analysis.summarize(samples, restarts, reserve)
    state, reasons = analysis.verdict(summary)
    progress(render(summary, state, reasons))
    with open(out_path + ".summary.json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "verdict": state, "reasons": reasons}, fh, indent=1)
    return {"PASS": 0, "FAIL": 1}.get(state, 2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relay", type=relay_url, default="http://127.0.0.1:8088",
                    help="relay control base URL (tailnet IP works from another machine)")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--every", type=float, default=15.0,
                    help="minutes between restarts; keep it slow for YouTube (429)")
    ap.add_argument("--sample-every", type=float, default=10.0, help="seconds")
    ap.add_argument("--feed", default=None, help="pin a feed instead of following on-air")
    ap.add_argument("--out", default="runtime/restart-soak.jsonl")
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    return run(a.relay, a.hours, a.every, a.sample_every, a.out, feed=a.feed)


if __name__ == "__main__":
    sys.exit(main())

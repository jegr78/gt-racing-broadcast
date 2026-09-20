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
    python3 tools/relay-restart-soak.py --replay runtime/restart-soak-<stamp>.jsonl

**This restarts a live feed.** Eight restarts of whatever is on air is what the defaults
amount to, and the relay's `/status` cannot tell an armed-idle machine from one streaming
to YouTube. It asks before it starts; `--yes` skips the prompt for an unattended run.

What it does, every cycle: sample `/status` every `--sample-every` seconds, and every
`--every` minutes trigger `GET /reload/<feed>` on the measured feed. That is the
director's restart in place, which is a restart with a consumer still attached, which
is exactly the case `should_obs_reconnect` covers.

**Space YouTube restarts out.** Reloading the same YouTube URL back to back is an
immediate 429 from the same IP, and a throttle outlasts the soak. Twitch tolerates a
closer cadence, but never closer than two minutes: each restart needs a recovery window
to be judged in and the next one needs a baseline window after that, and a run spaced
tighter cannot judge itself. It is refused rather than left to produce blanks.

Every sample and every restart goes to the JSONL at `--out`, so an interrupted run can
still be turned into a verdict with `--replay`. The verdict comes from
`tools/restart_soak.py`, which is pure and unit-tested in `tests/test_restart_soak.py`;
every threshold there is a quantity the relay already defines (the reserve from
`/status`, the deadline from the heartbeat).

Exit code: 0 on PASS, 1 on FAIL, 2 on UNKNOWN or a setup error.
"""
import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import restart_soak as analysis   # noqa: E402

UA = {"User-Agent": "racecast-restart-soak/1.0"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A relay answers on the host it was asked on, or the answer is not the relay's.

    `/status` carries the feed stream URLs, which the repo keeps inside the tailnet
    everywhere else. Following a 3xx elsewhere would hand them to whoever sent it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code,
                                     f"refusing a redirect to {newurl}", headers, fp)


# No proxy either, for the same reason: `relay_url` governs the URL the operator types,
# not where the bytes end up, and http_proxy would quietly reroute them.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def get_json(base, path, timeout=8.0):
    """GET one relay endpoint. Returns (payload, None) or (None, reason). Never raises:
    a soak must survive a blip and say so in the sample, not die three hours in.

    `http.client.HTTPException` is in the tuple deliberately. urllib wraps only the
    request in `URLError`; `getresponse()` and `read()` are not wrapped, so a truncated
    body (`IncompleteRead`) or a garbled status line derives from `Exception` alone and
    would kill a run that has already cut the live feed a dozen times."""
    try:
        req = urllib.request.Request(base + path, headers=UA)
        with OPENER.open(req, timeout=timeout) as resp:   # noqa: S310 (scheme checked)
            return json.loads(resp.read().decode("utf-8", "replace")), None
    except (urllib.error.URLError, OSError, ValueError,
            http.client.HTTPException) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def confirm(base, feed, restarts, ask=input, progress=print):
    """Say out loud what is about to happen to a live feed, and get a yes."""
    progress(f"about to restart {feed} on {base}, {restarts} time(s). "
             f"If that relay is on air, this cuts the broadcast feed each time.")
    if not sys.stdin.isatty():
        progress("not a terminal: pass --yes to run this unattended.")
        return False
    return ask("type yes to continue: ").strip().lower() == "yes"


def run(base, hours, every_min, sample_every_s, out_path, feed=None,
        clock=time.monotonic, sleep=time.sleep, progress=print):
    if os.path.exists(out_path):
        # Checked before the relay is touched: a soak never overwrites a recorded run,
        # and finding that out after the first restart would be a night's data too late.
        progress(f"{out_path} already exists; a soak never overwrites one. "
                 f"Pass a different --out.")
        return 2
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
    end_at = t0 + hours * 3600.0
    last_restart_at = end_at - analysis.RECOVERY_DEADLINE_S   # watch every window out
    next_restart = t0 + every_min * 60.0
    samples, restarts = [], []
    with open(out_path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": 0.0, "event": "start", "reserve_s": reserve,
                             "feed": target, "relay": base}) + "\n")
        # --hours is a minimum: the loop outstays it until the last restart's window
        # has closed, because a window the run stops short of cannot be judged at all.
        while (clock() < end_at
               or (restarts
                   and clock() - t0 <= restarts[-1] + analysis.RECOVERY_DEADLINE_S)):
            now = clock()
            st, why = get_json(base, "/status")
            row = analysis.sample_of(st, now - t0, feed=feed) if st else {
                "t": round(now - t0, 1), "error": why}
            samples.append(row)
            fh.write(json.dumps(row) + "\n"); fh.flush()
            if now >= next_restart and now <= last_restart_at:
                # The feed this sample MEASURED, not the one resolved before the loop.
                # A handover moves the on-air feed mid-soak, and reloading the boot-time
                # one would restart an off-air feed while the numbers came from another:
                # state_age_s never falls, the run says UNKNOWN for a reason that is not
                # true, and on YouTube the pointless re-pulls invite a 429 as well.
                hit = feed or row.get("feed") or target
                _res, rwhy = get_json(base, analysis.reload_path(hit))
                # AFTER the request, not before it: the sample above was read while the
                # feed was still up, and timestamping the restart at that reading put a
                # pre-restart value inside the recovery window. Rounded once and used
                # for both the analysis and the record, so --replay judges the same
                # windows the live run did.
                at = round(clock() - t0, 1)
                restarts.append(at)
                fh.write(json.dumps({"t": at, "event": "restart", "feed": hit}) + "\n")
                fh.flush()
                progress(f"  t+{at:6.0f}s  restart {len(restarts)} on {hit}"
                         + (f" (failed: {rwhy})" if rwhy else ""))
                next_restart = at + every_min * 60.0
            sleep(sample_every_s)

    return report(samples, restarts, reserve, out_path, progress=progress)


def report(samples, restarts, reserve, out_path, progress=print):
    """Analyse, print and persist the verdict. Shared by a live run and a replay."""
    summary = analysis.summarize(samples, restarts, reserve)
    state, reasons = analysis.verdict(summary)
    progress(analysis.render(summary, state, reasons))
    with open(out_path + ".summary.json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "verdict": state, "reasons": reasons}, fh, indent=1)
    return {"PASS": 0, "FAIL": 1}.get(state, 2)


def replay(path, progress=print):
    """Re-judge a recorded run. The reason this exists: the verdict used to appear only
    when the loop ran to its end, so a Ctrl-C three hours in left a file nothing read."""
    rows, dropped = [], 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                # A run killed mid-write leaves a half line, and that run is the whole
                # reason this exists. Drop it and judge the rest.
                dropped += 1
    if dropped:
        progress(f"skipped {dropped} unreadable line(s) at the end of an interrupted run")
    reserve = next((r.get("reserve_s") for r in rows if r.get("event") == "start"), None)
    if reserve is None:
        progress(f"{path} has no start record, so the reserve it ran with is unknown")
        return 2
    samples, restarts = analysis.split_replay(rows)
    progress(f"replaying {path}: {len(samples)} samples, {len(restarts)} restarts")
    return report(samples, restarts, reserve, path, progress=progress)


def default_out():
    return os.path.join("runtime", f"restart-soak-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relay", type=analysis.relay_url, default="http://127.0.0.1:8088",
                    help="relay control base URL (tailnet IP works from another machine)")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--every", type=float, default=15.0,
                    help="minutes between restarts; keep it slow for YouTube (429)")
    ap.add_argument("--sample-every", type=float, default=10.0, help="seconds")
    ap.add_argument("--feed", default=None,
                    help="pin a feed instead of following on-air (restarted AND measured)")
    ap.add_argument("--out", default=None, help="default: runtime/restart-soak-<stamp>.jsonl")
    ap.add_argument("--replay", default=None,
                    help="re-judge a recorded run instead of driving a relay")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a = ap.parse_args(argv)

    if a.replay:
        return replay(a.replay)

    # A recovery window plus a baseline window, both quantities the module already
    # defines. At exactly one recovery window the next baseline collapses to a single
    # reading, and a single reading is where the sawtooth happened to be, not a floor.
    floor_min = (analysis.RECOVERY_DEADLINE_S + analysis.BASELINE_WINDOW_S) / 60.0
    if a.every < floor_min:
        print(f"--every {a.every} leaves no room between restarts: a window needs "
              f"{analysis.RECOVERY_DEADLINE_S:.0f} s to be judged and the next baseline "
              f"another {analysis.BASELINE_WINDOW_S:.0f} s after it. "
              f"Use --every {floor_min:g} or more.")
        return 2
    planned = analysis.planned_restarts(a.hours, a.every)
    if planned < 1:
        print(f"--hours {a.hours} fits no restart the run could watch out "
              f"({analysis.RECOVERY_DEADLINE_S:.0f} s are needed after the last one).")
        return 2
    what = f"feed {a.feed}" if a.feed else "the on-air feed"
    if not a.yes and not confirm(a.relay, what, planned):
        print("aborted.")
        return 2

    out = a.out or default_out()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    return run(a.relay, a.hours, a.every, a.sample_every, out, feed=a.feed)


if __name__ == "__main__":
    sys.exit(main())

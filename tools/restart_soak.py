#!/usr/bin/env python3
"""Pure analysis for the relay restart soak (#619). Maintainer only, NOT shipped.

`racecast obs benchmark` answers "does this host keep real time" over two restarts in
140 s. #619 asks a different question that only time can answer: **does every restart
heal itself, or does something accumulate over hours?** The producer's report is a
picture that drifts further behind across an event night, and a restart is the event
that was shown to park OBS deep in the ring (#614). Since #629/#630 the relay rejoins
OBS after one. Whether that holds for the twentieth restart is not provable in a lab
window of one minute.

The driver (`tools/relay-restart-soak.py`) owns the polling and the `/reload` calls.
Everything here is pure so it can be unit-tested without a relay, the same split as
`e2e_checks.py` against `e2e.py`.

Every threshold below is a quantity the system already defines. None is tuned here:

- the **reserve** is `RACECAST_FEED_PREBUFFER_S`, read live from `/status`. It is what
  the relay deliberately holds back, so "within one reserve of where it was" is the
  natural way to say a backlog came home.
- the **deadline** is two heartbeats. The relay classifies health once per
  `HEARTBEAT_INTERVAL_S` (30 s), so anything faster than that is invisible to its own
  detectors anyway, and twice that is the first interval a director could react to.
"""

import argparse
import urllib.parse

HEARTBEAT_INTERVAL_S = 30.0          # mirrors the relay's own heartbeat
RECOVERY_DEADLINE_S = 2 * HEARTBEAT_INTERVAL_S
BASELINE_WINDOW_S = 60.0             # how far back a pre-restart baseline looks


def _backlogs(samples):
    return [s for s in samples if s.get("backlog_s") is not None]


def baseline_before(samples, t, window_s=BASELINE_WINDOW_S, not_before=None):
    """The settled backlog just before `t`: the LOWEST reading in the preceding window.

    The floor, not the mean, for the same reason `take_backlog_floor` uses it (#583): on
    5 s HLS segments the raw value is a sawtooth, and the floor is the part a slow
    consumer pushes up.

    `not_before` clamps the window so it cannot reach back into the PREVIOUS restart.
    Right after a rejoin the backlog dips to 0.0, because OBS sits at the live edge with
    nothing buffered yet. A floor that swallowed that dip called 0.0 the settled state,
    and then nothing could return to "within one reserve of 0.0". Measured on the
    production host at a two-minute cadence: the second restart got a baseline of 0.0 s
    and failed while it had in fact recovered. None when nothing was measured."""
    start = t - window_s
    if not_before is not None:
        start = max(start, not_before)
    vals = [s["backlog_s"] for s in _backlogs(samples) if start <= s["t"] < t]
    return min(vals) if vals else None


def recovery(samples, restart_t, base_s, reserve_s, deadline_s=RECOVERY_DEADLINE_S):
    """How a single restart played out: (peak_s, recovered_after_s, ok).

    `recovered_after_s` is the seconds from the restart until the backlog first came
    back to within one reserve of `base_s` and STAYED there for the rest of the
    deadline. Staying matters: a single sample dipping to baseline while the trend
    climbs is not a recovery, and that is exactly the shape a rejoin-then-drift would
    have. None when it never settled inside the deadline, which is also `ok` False.

    The window starts STRICTLY after the restart. The driver samples and then reloads
    inside one cycle, so a sample carrying `t == restart_t` was read before the request
    went out; counting it made "back after" a pre-restart reading, reported as tenths of
    a second.

    `ok` is None, not False, when there is nothing to judge: no baseline, no samples, or
    a window the run did not watch to its end. That last one is not pedantry. A restart
    fired in the closing seconds of a soak leaves a handful of samples, and the same
    shape produced a false PASS (the spike fell between two of them) and a false FAIL
    (five seconds of evidence). Neither is an answer."""
    if base_s is None or reserve_s is None:
        return None, None, None
    after = [s for s in _backlogs(samples) if restart_t < s["t"] <= restart_t + deadline_s]
    if not after:
        return None, None, None
    peak = round(max(s["backlog_s"] for s in after), 1)
    watched_to = max((s["t"] for s in samples if s.get("t") is not None), default=None)
    if watched_to is None or watched_to < restart_t + deadline_s:
        return peak, None, None
    limit = base_s + reserve_s
    settled = None
    for i, s in enumerate(after):
        if s["backlog_s"] <= limit and all(x["backlog_s"] <= limit for x in after[i:]):
            settled = round(s["t"] - restart_t, 1)
            break
    return peak, settled, settled is not None


DRIFT_MIN_POINTS = 4     # two halves of two: the fewest that can carry a trend


def drift(records, reserve_s, min_points=DRIFT_MIN_POINTS):
    """(amount_s, ok) for the whole run: how much higher the run ENDED than it started.
    This is the accumulation #619 asks about, and the reason the soak runs for hours. A
    single restart that recovers tells you nothing about the twentieth.

    The two halves' means, not the first and last value. `backlog_s` saws with the
    segment cadence, so two endpoints measure where the saw happened to be: a five-minute
    live run with two restarts reported 3.2 s of "drift" between baselines of 1.4 and
    4.6 s, both of which were the same healthy state.

    `ok` is None below `min_points`, because a trend needs more than two numbers and
    "not enough data" must not read as "no drift". The amount is still reported, and a
    negative one is kept: a run that ends lower is information, not a failure."""
    bases = [r["baseline_s"] for r in records if r.get("baseline_s") is not None]
    if len(bases) < 2 or reserve_s is None:
        return None, None
    half = len(bases) // 2          # >= 1: len(bases) >= 2 is established above
    first, second = bases[:half], bases[-half:]
    amount = round(sum(second) / len(second) - sum(first) / len(first), 1)
    if len(bases) < min_points:
        return amount, None
    return amount, amount <= reserve_s


def restart_observed(samples, restart_t, window_s=RECOVERY_DEADLINE_S):
    """Did the feed actually restart, or did the request do nothing?

    This exists because a soak that triggers nothing passes. `Feed.reload()` today kills
    the streamlink process even when the URL is unchanged, so a `/reload` is a real
    restart. If that ever stops being true, every window would sit at its baseline and
    the run would read PASS on a test that never ran.

    The signal is `state_age_s` falling, not the state string. A re-serve took 4.3 to
    5.5 s on both measured hosts while the soak samples every 10 s by default, so the
    `connecting` phase is easy to sample straight past. The age resets whatever the
    cadence. None when the relay reported no age to compare."""
    ages = [s for s in samples
            if s.get("age_s") is not None and restart_t <= s["t"] <= restart_t + window_s]
    before = [s for s in samples if s.get("age_s") is not None and s["t"] < restart_t]
    if not ages or not before:
        return None
    return any(s["age_s"] < before[-1]["age_s"] for s in ages)


def snap_growth(samples):
    """Ring laps over the run. A lap means the ring overran a consumer and bytes were
    dropped out from under OBS (#582), so any growth is a finding on its own."""
    vals = [s["snaps"] for s in samples if s.get("snaps") is not None]
    return (vals[-1] - vals[0]) if len(vals) >= 2 else None


def summarize(samples, restart_times, reserve_s):
    """The whole run: one record per restart plus the run-level checks. Pure."""
    records = []
    prev_end = None
    for t in restart_times:
        base = baseline_before(samples, t, not_before=prev_end)
        prev_end = t + RECOVERY_DEADLINE_S   # the next baseline starts after this window
        peak, after, ok = recovery(samples, t, base, reserve_s)
        records.append({"restart_t": round(t, 1), "baseline_s": base,
                        "peak_s": peak, "recovered_after_s": after, "recovered": ok,
                        "observed": restart_observed(samples, t)})
    amount, drift_ok = drift(records, reserve_s)
    snaps = snap_growth(samples)
    return {"restarts": records, "reserve_s": reserve_s,
            "drift_s": amount, "drift_ok": drift_ok,
            "snaps": snaps, "snaps_ok": None if snaps is None else snaps == 0,
            "samples": len(samples)}


def verdict(summary):
    """PASS, FAIL or UNKNOWN with the reasons that decided it. UNKNOWN is a real answer:
    a run that measured nothing must not read as a pass, and neither must a run that
    measured only part of what it triggered. PASS means every restart was watched out
    and came home; anything less is UNKNOWN, except a genuine failure, which outranks a
    gap because it is the more actionable answer either way."""
    reasons, judged = [], False
    unseen = [r for r in summary["restarts"] if r.get("observed") is False]
    if unseen:
        # Not a failure of the relay: a failure of the test to have run at all.
        return "UNKNOWN", [f"{len(unseen)} restart(s) never restarted the feed; "
                           f"the soak measured nothing there"]
    failed = [r for r in summary["restarts"] if r["recovered"] is False]
    graded = [r for r in summary["restarts"] if r["recovered"] is not None]
    ungraded = [r for r in summary["restarts"] if r["recovered"] is None]
    if graded:
        judged = True
        if failed:
            reasons.append(f"{len(failed)} of {len(graded)} restarts did not come back "
                           f"within {RECOVERY_DEADLINE_S:.0f} s")
    if summary["drift_ok"] is not None:
        judged = True
        if not summary["drift_ok"]:
            reasons.append(f"the baseline drifted up {summary['drift_s']} s over the run, "
                           f"more than the {summary['reserve_s']} s reserve")
    if summary["snaps_ok"] is not None:
        judged = True
        if not summary["snaps_ok"]:
            reasons.append(f"the ring lapped a consumer {summary['snaps']} time(s)")
    # A real failure outranks a gap: it is the more actionable answer either way.
    if reasons:
        return "FAIL", reasons
    if ungraded:
        # One graded restart used to be enough to call the whole run a pass, so a soak
        # could report PASS with most of its windows at n/a. Measured: three restarts
        # spaced closer than the deadline produced one grade, two blanks and a PASS.
        return "UNKNOWN", [f"{len(ungraded)} of {len(summary['restarts'])} restart "
                           f"windows could not be judged"]
    if not judged:
        return "UNKNOWN", ["nothing was measured"]
    return "PASS", []


def relay_url(value):
    """argparse type: a http(s) base URL, so a typo cannot become a file or ftp fetch."""
    parts = urllib.parse.urlparse(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise argparse.ArgumentTypeError(f"not an http(s) URL: {value!r}")
    return value.rstrip("/")



def reload_path(feed):
    """The /reload route for one feed, with the name quoted.

    The name comes from an operator flag or from the relay's own `/status`. Unquoted, a
    stray space is enough for `http.client` to raise `InvalidURL` and kill the run, and
    a `?` or `#` would change which endpoint is hit."""
    return "/reload/" + urllib.parse.quote(str(feed), safe="")


def planned_restarts(hours, every_min):
    """How many restarts fit in a run, counting only those it can watch out.

    A restart fired with less than the recovery deadline left is judged on whatever few
    samples remain, and that shape produced both a false PASS and a false FAIL. The
    driver does not fire one it cannot observe, so this is also what the operator is
    asked to confirm."""
    if every_min <= 0:
        return 0
    span = hours * 3600.0 - RECOVERY_DEADLINE_S
    return max(0, int(span // (every_min * 60.0)))


def sample_of(status, t, feed=None):
    """One row from a /status payload: the measured feed's backlog, inbound gap and snaps.

    Without `feed` the on-air one is resolved per sample rather than pinned at the start,
    because a handover or a takeover moves it and the soak must follow. With `feed` the
    measurement is pinned to the same feed the driver restarts: pinning only the restart
    target left the numbers coming off a different feed, whose `state_age_s` never fell,
    and the run said UNKNOWN for a reason that was not true. Pure."""
    live = (status or {}).get("live") or {}
    name = feed or live.get("feed")
    feed = ((status or {}).get("feeds") or {}).get(name) or {}
    return {"t": round(t, 1), "feed": name, "state": feed.get("state"),
            "quality": feed.get("quality"), "platform": feed.get("platform"),
            "age_s": feed.get("state_age_s"),
            "backlog_s": feed.get("backlog_s"),
            "gap_s": feed.get("inbound_max_gap_s"),
            "snaps": feed.get("consumer_snaps")}



def _never_left_band(record, reserve_s):
    """True when the peak stayed inside baseline + reserve, so no recovery took place."""
    base, peak = record.get("baseline_s"), record.get("peak_s")
    if base is None or peak is None or reserve_s is None:
        return False
    return peak <= base + reserve_s


def split_replay(rows):
    """A recorded run read back: (samples, restart_times).

    The JSONL used to hold samples only, so the file never said WHEN a restart happened
    and the analysis could not be reproduced from it — which also meant an interrupted
    soak left hours of readings that nothing could turn into a verdict. The driver now
    writes a marker row per restart and this splits the two apart again. Pure."""
    samples = [r for r in rows if not r.get("event")]
    restarts = [r["t"] for r in rows
                if r.get("event") == "restart" and r.get("t") is not None]
    return samples, restarts


def render(summary, state, reasons):
    """The operator-facing table. One line per restart, then the run-level checks."""
    out = [f"restart soak: {summary['samples']} samples, reserve "
           f"{summary['reserve_s']} s, deadline {RECOVERY_DEADLINE_S:.0f} s",
           "   at        baseline   peak      back after   ok"]
    for r in summary["restarts"]:
        def f(v, unit=" s"):
            return "n/a" if v is None else f"{v}{unit}"
        ok = {True: "yes", False: "NO", None: "n/a"}[r["recovered"]]
        back = f(r["recovered_after_s"])
        if r["recovered"] and _never_left_band(r, summary["reserve_s"]):
            # Nothing to come back from. A number here reads as a measurement of the
            # rejoin, and a near-zero one reads as a suspiciously good measurement.
            back = "in band"
        out.append(f"   {r['restart_t']:>7.0f}s  {f(r['baseline_s']):>8}   "
                   f"{f(r['peak_s']):>7}   {back:>9}   {ok}")
    out.append(f"   baseline drift over the run: "
               f"{'n/a' if summary['drift_s'] is None else str(summary['drift_s']) + ' s'}"
               f"   ring laps: {summary['snaps'] if summary['snaps'] is not None else 'n/a'}")
    out.append(f"   => {state}" + (": " + "; ".join(reasons) if reasons else ""))
    return "\n".join(out)



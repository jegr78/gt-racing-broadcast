# Fan-out backpressure + OBS freeze/stutter detector — design

**Status:** design (approved direction: hybrid — bounded backpressure + cursor-progress
detector). 2026-07-15.

## Problem

In fan-out mode the relay is the persistent HTTP server per feed; OBS's ffmpeg media
source keeps one open socket to the feed port. A **byte discontinuity** in that stream
desynchronises OBS's demuxer → the picture freezes/stutters (~1 Hz), and OBS does **not**
self-heal. Only the manual "OBS Feed Reset" (`release_feed_inputs`, a source rebuild)
recovers it. Confirmed live 2026-07-15 with a deterministic repro (ARM→STOP→ARM on the
on-air feed) and instrumented with `tools/obs-freeze-probe.py`.

**Critically, every metric #488 relies on is BLIND to this** (measured, live feed):

| Phase | OBS `mediaCursor` Δ / 1 s | screenshot | `renderSkip` / fps |
|---|---|---|---|
| clean live | steady **~+1070 ms (≈1.0×)** | CHG each tick | 0 % / 60 |
| frozen | **+0 ms** | SAME | **0 % / 60** |
| stutter (stale demuxer) | **choppy: 0 / 2000 / 0 …** | some SAME | **0 % / 60** |
| at OBS reset | large negative jump, img n/a | — | one-tick ~4.6 % blip |
| after reset | steady **~+1070 ms (≈1.0×)** | CHG each tick | 0 % / 60 |

`mediaState` is always `PLAYING` (useless); `renderSkippedFrames` is a compositor
render-timing metric that never moves for a source-side freeze. **The reliable signal is
OBS `mediaCursor` progress** (Δcursor/Δwall): ≈1.0 steady = healthy, ≈0 or choppy =
frozen/stutter.

## Two discontinuity sources (both must be covered)

1. **Ring snap (drift class, ~90 min).** `FeedRing` (16 MB, ~12 s) has a **non-blocking
   writer**: on overflow it drops the oldest bytes and any behind reader **snaps to base**
   (`FeedRing.read`), dropping bytes *out from under a slightly-behind OBS* → discontinuity.
   Relay-visible: `FeedFanoutServer.consumer_health()` already counts per-consumer
   `snaps` — but that value is currently **unused** (abandoned when #488 disproved the
   *send-block* signal; the snap-count was discarded with it).
2. **Streamlink-restart splice (ARM/STOP class).** A deliberate re-serve (ARM/RELOAD/
   quality) restarts streamlink; the fresh stream is spliced onto OBS's still-open socket.
   This is **not** a ring snap, so `snaps` won't catch it — only OBS's cursor stalling does.

Backpressure can prevent (1) but not (2). Hence the hybrid.

## Design

### Part A — bounded backpressure for the OBS reader (prevents the drift snap)

Make the ring writer refuse to overflow **past the critical (OBS) consumer's cursor**,
within a latency budget:

- Register the OBS HTTP consumer(s) as **critical** readers whose cursor the ring tracks
  (the Director-Panel preview `_PreviewRingTap` stays **non-critical / droppable** — it must
  never be able to stall OBS or streamlink).
- On `write`, if dropping the overflow would cross the oldest critical cursor **and** that
  consumer's lag is within `BACKPRESSURE_BUDGET_BYTES` (≈2–3 s of stream): **block the
  writer** on the ring condition until the consumer advances, bounded by
  `BACKPRESSURE_MAX_WAIT_S`.
- If the consumer exceeds the budget (a truly wedged OBS): **give up** — snap as today AND
  flag the feed so Part B reconnects immediately. This bounds added live latency and keeps
  the original "a stuck reader can never wedge streamlink" safety.
- Only the on-air/critical feed applies backpressure; a slow preview never does.

Tuning constants (soak-tuned, env-overridable like the existing fan-out knobs):
`RACECAST_FEED_BACKPRESSURE` (default ON, `=0` restores today's non-blocking writer),
`RACECAST_FEED_BP_BUDGET_S`, `RACECAST_FEED_BP_MAX_WAIT_S`.

### Part B — cursor-progress detector + RESET (universal curative net)

In the heartbeat, replacing the **action** of the disproven `renderSkip` auto-resync (keep
recording renderSkip for the health chart, just stop acting on it):

- For the feed the relay reports **serving** (bytes flowing), sample OBS
  `GetMediaInputStatus.mediaCursor` each heartbeat.
- Compute the **cursor-progress ratio** `Δcursor / Δwall` over a short sliding window.
  Debounced + cooldown-gated (mirror `render_drift_decision`).
- Fire `_obs_reconnect` (the existing RESET primitive) when the ratio stays **well below
  ~1×** (frozen or choppy-stall) while serving. Recovery is verifiable in-band: the ratio
  returns to ~1× within ~2 s.
- **Debounce the ~6 s connecting→serving lag** after an ARM (measured) so a normal
  activation never trips it.
- Additionally consume the now-live `consumer_health().snaps` as an **early trigger** for
  the drift class (a jump in snaps ⇒ a discontinuity happened ⇒ reconnect), cheaper than
  the OBS round-trip.

### Update (#582, 2026-09) — snaps are a record, and the rebuild has an effectiveness guard

The Catalunya qualifying of 2026-08-28 showed the early trigger at its worst: OBS drained
the ring about 20 % slower than real time, the ring lapped it every ~2 minutes, and each
lap fired a rebuild. 26 rebuilds in 56 minutes, each a black dropout, none of which could
help a consumer that is chronically slower than real time. So:

- **`snap_early_trigger` is gone as a trigger.** Its successor `consumer_overflowed` is read
  once per heartbeat and only records a `fanout_overflow` health event (health monitor +
  post-event report). It never rebuilds.
- **Every automatic rebuild is judged.** `RebuildGuard` (pure) looks at the first full
  cursor window after a rebuild: a stall fraction below `RACECAST_FEED_FREEZE_FRAC` means
  it helped and resets the streak; anything at or above it (including "better but still
  stalling") counts as ineffective. After `REBUILD_GUARD_MAX_ATTEMPTS` (3) ineffective
  rebuilds in a row the sampler stands down: a yellow health reason with the OBS frame
  rate, an `obs_rebuild_stood_down` event, and no further rebuilds.
- **Re-arm.** The next stint change (a new on-air feed or pull index) lifts the stand-down,
  and so does the Director Panel's RE-ARM (`POST /obs/rebuild-rearm`, director-gated).
- **Scope.** The guard covers the consumer-side rebuild only. The re-serve rejoin
  (`should_obs_reconnect`) answers a restart on the input side, repeats legitimately and
  already pages on churn; the #493 step-down, auto-cover and auto-failover fire once per
  outage. Since #614 that rejoin covers two cases, not one: a dead streamlink (`dropped`)
  and any restart with a consumer still attached to the ring — the director's `/reload`
  or tier change, and a `set_index` on the feed OBS is on air with, which includes the
  solo and qualifying single-feed `/next`. The ping-pong handover is still excluded, for
  the original reason: `close_when_inactive` means OBS has already dropped the off-air
  feed, so there is no open demuxer to splice into and nothing to rebuild.
  That rejoin is also **delayed** by `FEED_PREFETCH_LAND_S` (5 s, the value
  `obs_benchmark.PREFETCH_LAND_S` already used for the #584 run). The ring's time index
  is byte **arrival** time, so streamlink's HLS prefetch burst lands entirely inside the
  3 s trailing mark and an immediate rejoin would put OBS at the burst's start — a clean
  demuxer 10–19 s behind live. Waiting it out lets the burst age past the mark. The wait
  is skipped for a local capture feed (#592), whose ffmpeg has no burst, and
  `RACECAST_FEED_PREFETCH_LAND_S=0` restores the immediate rejoin for a source where the
  wait is not worth it (Twitch low-latency, where the value is uncalibrated). A rejoin
  that no longer belongs to the running serve no-ops (`rejoin_is_stale`): rebuilding
  would drop OBS onto the newest serve's burst, or — when the serve died inside the wait
  — onto a feed with no bytes at all.

## Test strategy (TDD)

Pure, unit-testable decision functions (like `render_drift_decision`):

- `cursor_progress_decision(samples, *, ratio_floor, debounce, cooldown, since_last)` →
  bool. Fixtures come straight from the recorded probe phases (clean 1.0×, frozen +0,
  choppy stutter, post-reset 1.0×) — assert it fires on frozen/stutter, not on clean or the
  connecting lag.
- `backpressure_write_decision(overflow, critical_lag_bytes, budget_bytes)` → `block` /
  `snap` — assert block within budget, snap+flag past budget, and that a non-critical reader
  never causes a block.
- `snap_early_trigger(prev_snaps, snaps)` → bool.

The relay wiring (threads/sockets/OBS round-trip) stays thin around these, per the existing
fan-out pattern. `tools/obs-freeze-probe.py` (this investigation's harness) is committed as
the live validation tool and re-run against the ARM/STOP repro to confirm the detector
fires and the RESET/BP loop holds.

## Non-goals / notes

- Not touched: the direct-serve (`RACECAST_FEED_FANOUT=0`) path (no shared ring, OBS's
  socket closes on a streamlink restart, so no stale demuxer — the fallback stays safe).
- Backpressure is scoped to the OBS consumer; the preview tap contract is unchanged.
- Keep both env kill-switches so a producer can revert either half independently.

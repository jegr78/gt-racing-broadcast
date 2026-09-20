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
  That rejoin is also **delayed until the HLS prefetch burst has landed**. The ring's
  time index is byte **arrival** time, so the burst lands entirely inside the trailing
  mark and an immediate rejoin would put OBS at the burst's start — a clean demuxer
  10–19 s behind live.

  **How long the burst takes to arrive is a download duration**, set by the producer's
  downlink, the source's bitrate and the CDN. A constant measured on one machine is
  therefore wrong on every other, which is how a flat 5 s ended up about 3 s short for
  YouTube ROBUST — the very tier the #493 auto step-down moves to. So the relay
  **measures it per serve**: the rejoin thread watches `last_byte_ts` and takes the first
  inbound idle of `BURST_IDLE_S` as the burst's end, then waits `prebuffer_s`, then
  rebuilds. Detecting the end costs `BURST_IDLE_S`, and that latency is itself the safety
  margin.

  Two constants remain fixed, and both describe the **source's segment cadence** rather
  than the connection. Measured 2026-09-20 with `tools/prefetch-burst-probe.py`, which
  runs the relay's own serve flags against a live source and needs neither a relay nor a
  league:

  | platform | tier | segments | runs | burst arrival | worst per segment | gaps inside the burst | steady cadence |
  |---|---|---|---|---|---|---|---|
  | YouTube | FULL | 4 | 7 | 0.72–1.82 s | 0.46 s | ≤ 0.78 s | ~5 s |
  | YouTube | ROBUST | 6 | 4 | 1.48–**4.96** s | 0.83 s | ≤ 0.78 s | ~5 s |
  | Twitch | FULL | 2 | 6 | 0.45–0.69 s | 0.35 s | < 0.5 s | 1.4–1.9 s |
  | Twitch | ROBUST | 2 | 9 | 0.50–**1.80** s | 0.90 s | < 0.5 s | 1.4–1.9 s |

  - **`BURST_IDLE_S` = 1.0 s** — the idle that ends the burst. It has to exceed the gaps
    *inside* a burst and stay below the steady cadence, so the usable window is about
    (0.78, 1.4) and 1.0 fits both platforms. A slow link widens the intra-burst gaps and
    can trip it early; the rejoin then lands in the burst's tail and sheds most of it,
    the same graceful degradation as a wait that is slightly short.
  - **`SEGMENT_FETCH_BUDGET_S` = 1.0 s** — a **ceiling**, not an estimate:
    `prefetch_land_s(segments, prebuffer_s)` bounds the wait for a source that never
    pauses long enough to be detected. Twitch low-latency is exactly that source: at a
    2 s threshold it showed no gap in a 40 s window. 1.0 covers every measured worst
    case; both ROBUST maxima are single outliers about 3× their own median (six further
    Twitch ROBUST runs all landed at 0.50–0.55 s), so the ceiling is sized on the tail.

  The probe's own burst-splitting threshold is **per platform** and cannot be one value:
  2.0 s splits YouTube correctly and never fires on Twitch, 0.5 s splits Twitch correctly
  and chops YouTube's burst apart. Both failures print a plausible number, so the probe
  now picks the threshold by platform and reports a degenerate split as unusable.

  A local capture feed (#592) has no `--hls-live-edge` and never waits. A rejoin that no
  longer belongs to the running serve no-ops (`rejoin_is_stale`), checked during the
  burst wait as well as after it: rebuilding would drop OBS onto the newest serve's
  burst, or — when the serve died inside the wait — onto a feed with no bytes at all.
  `racecast obs benchmark` has no live byte signal of its own, so it waits the full
  ceiling and reads the real prebuffer from `/status`'s `feed_prebuffer_s`. It carried
  the same too-short 5 s, so the ROBUST windows of a #584 run taken before this change
  may have sampled a backlog the benchmark caused itself.

  **The benchmark's backlog column can describe the join rather than the host.** It
  starts sampling `settle_s` after the rejoin, and on a *fresh* serve that still lands
  inside the CDN's DVR walk: streamlink fetches as fast as it is allowed until it
  reaches the live edge, so media arrives faster than real time and `backlog_s`, which
  ages by ARRIVAL, climbs while nothing downstream is slow. Measured on the Windows
  production host 2026-09-20: a window opened one minute after arming reported
  15.5 s → 57.2 s with OBS at 0.99x playback, 60.0 fps and 0.58 ms render. Left
  undisturbed for three minutes afterwards, the same feed sat flat at 3.7 to 4.6 s,
  which matches the 4.5 to 5.7 s of #619 and the 0.2 to 1.9 s of the same run on the
  Mac. The host was never behind. `summarize` therefore reports `source_ahead_s`, the
  part of the rise the consumer cannot account for (a consumer adds at most
  `(1 - playback_rate) * duration`), and the report names it. The verdict is unchanged:
  the backlog stays out of `keeps_real_time`, and this run is why that is right.

  `inbound_max_gap_s` does NOT settle this question, and reading it as a rate is a
  mistake worth writing down: it is the interval's **maximum** gap, so a burst of
  segments followed by one 5.3 s pause prints the same number as a steady 5.3 s cadence.
  It bounds the worst stall; it says nothing about throughput.

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

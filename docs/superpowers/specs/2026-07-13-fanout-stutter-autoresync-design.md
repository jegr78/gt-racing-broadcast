# Fan-out stutter: detection-driven auto-resync + graduated stall handling (#488)

**Date:** 2026-07-13

> **Superseded in part by #582 (2026-09).** The render-skip auto-resync this spec describes
> (Component 1 and the DESIGN PIVOT's detection) was deleted: it had not acted since the
> cursor-progress freeze detector became the default, and the render-skip rate stays a
> recorded diagnostic only. The `RACECAST_FEED_AUTORESYNC*` knobs are gone. See
> `2026-07-15-fanout-backpressure-and-freeze-detector-design.md` for the current detector
> and its effectiveness guard.
**Issue:** [#488](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/488)
**Scope:** `src/relay/racecast-feeds.py` (the fan-out `FeedRing` / `FeedFanoutServer` /
byte-stall watchdog / `Feed` serve loop), a new pure `tests/test_fanout.py`, and a new
maintainer soak tool under `tools/`. Machine-level `.env` flags. Single PR into `main`.

## Problem

During long broadcasts the program develops a visible **stutter/lag** that a Director-Panel
**"OBS Feed Reset"** clears every time. Reproduced across N24 event days (AWS cloud box,
v1.5.5). The post-event report proved the stutter is **100% ingest/source-side** — OBS
output was flawless (0% dropped, 60 fps, 0 congestion) — so the mispacing is in the
**source → relay ring → OBS-ffmpeg** path, never the encode/output.

Two distinct triggers, same fix family:

- **Trigger A — OBS-consumption drift.** The fan-out `FeedRing` has **no consumer
  backpressure**: the live reader (streamlink → ring) always appends and overflows the
  oldest bytes. Over a long session OBS's ffmpeg media-source consumption clock drifts
  against the live-edge, its socket backs up, and the ring eventually overflows past OBS's
  cursor → the cursor is snapped forward → OBS loses bytes → ffmpeg discontinuity = the
  visible glitch. **Interval is variable/unpredictable** (recurred within ~10 min once,
  ~90 min another) → no time-based trigger. A media-source rebuild resets OBS's demuxer at
  the live edge and clears it — reliably, every time, on both feeds.

- **Trigger B — the 8 s byte-stall watchdog amplifies a flaky source.** A commentator
  producing from a **PlayStation over WiFi** (a normal, recurring condition — jittery
  wireless + console encoder) periodically stalls the live edge. The relay's watchdog
  treats any > 8 s byte gap as a dead reader and forces a **full yt-dlp re-resolve**,
  whose teardown makes the visible gap *larger* than the underlying micro-stall; repeated
  cycles read as continuous stutter. streamlink already retries HLS segments internally, so
  the re-resolve is often premature.

## What already exists (so we don't rebuild it)

- **The proven reset primitive:** `Feed._obs_reconnect()` → `obs_ws.release_feed_inputs([port])`
  re-applies the feed input's own settings (`SetInputSettings`), a forced source rebuild
  that drops OBS's socket so it re-joins the fan-out at the live edge with a clean demuxer.
  This is exactly what the manual "OBS Feed Reset" button and the drop-recovery
  `on_first_byte` hook already call. Best-effort, threaded, never crashes the serve.
- **The ring already tracks absolute offsets:** `FeedRing.live_offset()` = `_base + len(_buf)`;
  each consumer carries its own `cursor`. The cursor-snap (`if cursor < self._base`) is the
  exact data-loss event.
- **The byte-stall watchdog** (`_serve_fanout` inner `_watchdog`, `feed_stalled`,
  `FANOUT_STALL_S = 8.0`) already detects a stall; today it kills unconditionally at 8 s.
- **The reader stamps `last_byte_ts`** on every block — a rolling bitrate (B/s) is free from it.

## Design

### Component 1 — Detection-driven auto-resync (Trigger A)

**Signal — how long OBS's handler is blocked draining the socket, plus cursor-snaps.**
Reading the real `FeedRing.read` corrects the naive "lag = live − cursor" idea: `read()`
returns **all** available bytes (`data = buf[cursor−base:]`) and advances the cursor to the
live edge on **every** call — so the backlog when OBS is slow lives in the blocking `sendall`
/ socket buffer, **not** in the ring cursor (which re-pins to live each read). The precise,
bitrate-free signals are therefore **time- and snap-based**, published per connection by
`FeedFanoutServer._serve`:

- **Stuck** — `stuck_s = now − cycle_ts`, where `cycle_ts` is stamped after each `read`
  *and* after each completed `sendall`. When OBS keeps up (or the ring is empty) `cycle_ts`
  refreshes ~1 Hz; when OBS's ffmpeg stops draining, `sendall` blocks and `cycle_ts` goes
  stale → `stuck_s` grows. **This is the drift manifesting** (the send-block lengthens as
  OBS falls behind).
- **Snap** — the definitive data-loss glitch: when a consumer fell so far behind that the
  ring overflowed past its cursor, `read` snaps it forward. The handler detects it with pure
  arithmetic — `skipped = (new_cursor − len(data)) − prev_cursor; snapped = skipped > 0` —
  **without changing `read`'s signature** (shared with the program-audio consumer).

A light per-feed sampler (folded into the existing 1 Hz `_serve_fanout` watchdog) reads the
OBS consumer's `(cycle_ts, snap_count)` from `FeedFanoutServer` (the preview ring-tap reads
the ring directly, not via the socket server, so it is never counted). Only samples while the
feed is **serving with an OBS consumer attached** (off-air → `close_when_inactive=True` → OBS
disconnected → no consumer → no trigger, naturally gated). **No rolling-bitrate estimate is
needed** — the signals are seconds and event counts.

**Pure decision function** (`tests/test_fanout.py`):
```python
def autoresync_decision(stuck_s, snaps, since_last_reset_s, *,
                        stuck_threshold, snap_threshold, cooldown_s):
    """True when OBS's handler is stuck draining the socket (stuck_s > stuck_threshold)
    OR has taken cursor-snaps (snaps >= snap_threshold), AND the cooldown since the last
    auto-reset has elapsed (since_last_reset_s >= cooldown_s). Pure — unit-tested with
    synthetic values. None stuck_s / zero snaps never trips it."""
```

**Action.** On a True decision the feed calls the existing `Feed._obs_reconnect()` (rebuild
the feed's OBS input → OBS re-joins clean at the live edge), records the reset time (for the
cooldown), resets the snap counter, and logs it. The cooldown prevents reset loops; a
stuck/dead OBS socket is dropped by the rebuild and OBS reconnects on its own.

**Default ON with a kill-switch** (`RACECAST_FEED_AUTORESYNC`, default on;
`=0` disables). It automates a proven-safe action and offloads the director; the brief
(1–2 s) black at each (rare) auto-reset is acceptable. Thresholds are conservative and
**calibrated in the local soak before it is relied on live**. Transport is a machine
concern, so the flags live in machine `.env`, consistent with `RACECAST_FEED_FANOUT`.

**Tuning knobs** (env overrides; the defaults are soak-tuned *starting points*, not final):
- `RACECAST_FEED_AUTORESYNC_STUCK_S` — OBS-handler send-block threshold, start **5.0 s**.
- `RACECAST_FEED_AUTORESYNC_COOLDOWN_S` — min seconds between auto-resets, start **60.0 s**.
- `snap_threshold` — a cursor-snap is already a real glitch (a full ring ≈ 12 s of video
  lost), so the default is **1** (reset on the first snap in a window), cooldown-gated.

**Ring headroom (now, low-risk).** Raise the per-feed ring from `FANOUT_RING_BYTES = 8 MB`
to **16 MB**. This is relay-side headroom — *not* OBS buffering — so the `lag_threshold`
sits comfortably **below** the hard cursor-snap point: the auto-resync always fires an
**orderly** rebuild before the ring would overflow into a jarring mid-stream snap. It also
absorbs brief jitter without any reset. Cheap and safe regardless of the backpressure
decision below.

### Component 2 — Graduated stall handling (Trigger B)

Make the byte-stall hard-kill grace **configurable and higher**: `RACECAST_FEED_STALL_S`
(start **20.0 s**, up from the hardcoded `FANOUT_STALL_S = 8.0`). A byte-stall while
streamlink is **alive** is often a recoverable uplink micro-stall that streamlink's own HLS
segment retry bridges without a teardown; the longer grace lets that recovery happen instead
of forcing a full yt-dlp re-resolve that enlarges the gap. `feed_stalled(last_byte_ts, now,
stall_s=…)` stays a pure, tested function — only the threshold becomes configurable (read
once at feed start). **Trade-off:** a genuinely dead source is re-resolved a bit later; the
30 s red health grace is separate and unchanged, so alarming is not delayed materially.

### Component 3 — Local soak harness (`tools/`, maintainer, not CI)

A multi-hour, real-OBS validation the unit suite cannot do (the drift is an ffmpeg-playout /
source-clock phenomenon). **Fully local — no cloud box, no real stream, no cookies.**

- Drives the **real** `FeedRing` + `FeedFanoutServer` classes (imported from
  `src/relay/racecast-feeds.py`), fed by **`ffmpeg -re`** (native-rate = ~1× live pacing;
  a test pattern or looped clip). `-re` avoids the VOD-race that no-backpressure otherwise
  causes. The operator points **local OBS** at the feed port.
- **Injectable stalls** (Trigger B): the harness can withhold bytes for a few seconds
  periodically to simulate the WiFi/console uplink stall.
- **Instrumentation:** logs the drift signal (`stuck_s`), snap count, and auto-reset firings
  over time so the drift is *visible*, the auto-reset's detect-and-clear is verifiable, and
  the drift-over-time curve can be **classified as clock-bias vs jitter** — the evidence that
  resolves the backpressure question (§below). Records the **fan-out on/off comparison** (AC).
- **Safe deterministic core** (in the unit suite, no OBS): the mechanism is validated by
  feeding the pure `autoresync_decision` and the sampler synthetic lag/stall values and
  asserting the reset fires and clears — this does **not** need the natural drift to occur.
  The natural-drift reproduction is **best-effort** and can be accelerated with injected
  pacing perturbations.

### The backpressure question — resolved within #488 by the soak (NOT deferred)

The issue frames "true consumer-paced backpressure on the ring" as the durable root fix.
**That framing is likely mistaken, and we resolve it with data inside this issue — no
deferred follow-up.**

Why it is likely the wrong tool for Trigger A: backpressure means the reader stops reading
streamlink when OBS is slow. But the source is a **live** stream — if the reader stops,
streamlink's stdout pipe fills, streamlink stops fetching segments, and it **falls behind
the live edge**. So backpressure on a live source buys either growing latency or data-loss
on resync; you cannot be both "paced to OBS" and "at the live edge" when OBS averages below
real-time. Two possible drift regimes decide whether backpressure helps at all:

- **Jitter around 1×** → a bounded buffer absorbs it; backpressure/more buffer *would* help.
- **Persistent clock bias** (OBS's clock vs the source encoder's clock differ by a tiny
  constant) → **no finite buffer helps**; the offset accumulates monotonically and a
  periodic resync is the only tractable fix — exactly what live players do. Here the
  detection-driven auto-resync is the *correct architecture*, and backpressure would
  actively hurt (relay falls behind live) for no benefit.

The live evidence points to **clock bias**: the stutter takes a variable 10–90 min, a
reset clears it *every time*, and it stays stable for a while after — the accumulate →
reset → stable → re-accumulate signature of a slow monotone drift, not one-off jitter.

**How #488 resolves it (in this PR, before the issue is closed):** the soak harness logs
`lag(t)` and classifies the curve:
- **Monotone/linear ramp → clock bias** → the auto-resync is confirmed *sufficient*; we
  document that finding and close #488. No backpressure is built (it would be futile and
  risks the live-critical "reader never blocks" invariant).
- **Oscillating with occasional spikes → jitter** → we add bounded-buffer backpressure /
  a larger read-ahead **in this same PR**, its exact shape driven by the measured curve,
  before closing #488.

Either branch is decided by the soak evidence and finished within #488 — nothing is carried
to a separate follow-up. The manual "OBS Feed Reset" button is unchanged throughout.

## Config flags (machine `.env`)

| Flag | Default | Meaning |
|---|---|---|
| `RACECAST_FEED_AUTORESYNC` | **on** | Detection-driven auto-resync; `=0` disables. |
| `RACECAST_FEED_AUTORESYNC_STUCK_S` | 5.0 | OBS send-block (stuck) threshold, soak-tuned. |
| `RACECAST_FEED_AUTORESYNC_COOLDOWN_S` | 60.0 | Min seconds between auto-resets. |
| `RACECAST_FEED_STALL_S` | 20.0 | Byte-stall hard-kill grace (was hardcoded 8 s). |

Plus a constant (not a flag): `FANOUT_RING_BYTES` **8 MB → 16 MB** (ring headroom, §Component 1).

## Testing & validation

**Pure units (`tests/test_fanout.py`, stdlib runnable script, CI):**
- `autoresync_decision`: stuck-only, snap-only, both, cooldown-blocked, None/zero inputs,
  boundary values.
- The snap arithmetic `snap_bytes(prev_cursor, new_cursor, data_len)` from a `FeedRing`
  scenario (synthetic offsets — no real stream): a consumer kept behind past the ring
  capacity reports a positive skip; a keeping-up consumer reports 0.
- The consumer `cycle_ts`/`stuck_s` bookkeeping on `FeedFanoutServer` via a fake blocking
  socket: a stuck sender grows `stuck_s`; a draining sender keeps it ~0.
- `RACECAST_FEED_AUTORESYNC*` / `RACECAST_FEED_STALL_S` parsing (default-on token logic like
  `fanout_enabled`; the `_env_float` positive-or-default guard).

**Graduated watchdog:** `feed_stalled` with the configurable threshold (new input cases in
`tests/test_pov.py`, where the fan-out health functions already live).

**Local soak (maintainer, blocking before any live reliance):** run the `tools/` harness
against local OBS — **harness built first** so a baseline run (fan-out on, no fix) can start
early and soak for hours in the background while the fixes are built; then a second run with
the fixes to confirm the auto-reset detects and clears the drift, and that the graduated
watchdog bridges an injected stall without a re-resolve. Fan-out on/off comparison recorded.

## Acceptance criteria

- [ ] A single feed can run a multi-hour session; when OBS-consumption drift sets in, the
      relay **detects** it (OBS handler stuck draining the socket, or a cursor-snap) and
      **auto-rebuilds** the feed's OBS input (the proven reset), clearing the stutter without
      the director watching. Default on, `RACECAST_FEED_AUTORESYNC=0` disables; manual Feed
      Reset unchanged.
- [ ] A recoverable multi-second source micro-stall (WiFi/console) no longer forces an
      immediate full re-resolve — the graduated `RACECAST_FEED_STALL_S` grace lets
      streamlink's internal retry bridge it.
- [ ] Pure unit coverage for `autoresync_decision`, the `snap_bytes` arithmetic + `stuck_s`
      bookkeeping, and the configurable stall threshold.
- [ ] A local `tools/` soak harness (ffmpeg `-re` + the real ring/server + local OBS,
      injectable stalls, lag/snap/reset logging) exists and is documented; the fan-out
      on/off behaviour is recorded from it. No cloud box required.
- [ ] The ring headroom bump (`FANOUT_RING_BYTES` 8→16 MB) keeps the auto-resync firing an
      orderly rebuild below the hard cursor-snap point.
- [ ] The backpressure question is **resolved inside #488** from the soak's `lag(t)`
      classification: clock-bias → auto-resync documented as sufficient; jitter → bounded
      buffer / read-ahead added in this same PR. Nothing carried to a deferred follow-up.

---

## DESIGN PIVOT (2026-07-13, after the reproduction study) — detection = obs-ws GetStats render-skip rate, NOT the socket signal

The socket-based detection above (`stuck_s` / cursor-snap, Component 1) was **empirically
disproven** as a trigger for the real drift. A multi-environment reproduction study
(Mac hardware-decode; Mac forced 4K60 + CPU load; a real 1080p60 YouTube stream + software
decode on the Mac; and a headless Ubuntu UTM VM rendering via software GL/llvmpipe)
established:

- **The relay socket signal is blind to OBS render/playout drift.** In the VM, OBS rendered
  at **4.8 fps with 64% render-skip**, yet the relay's `stuck_s` stayed **~0** — OBS reads
  the media socket greedily regardless of its render state, so the socket never backs up.
  Confirmed across every environment (`stuck_s` never exceeded ~1 s, even under extreme
  load; no cursor-snaps ever).
- **The signal that DOES reflect the drift is OBS's own `renderSkippedFrames`** (obs-ws
  `GetStats`): 0.2 % peak on the cloud box during the event stutter, 0.3 % on the idle Mac,
  64 % under VM overload — it tracks the OBS render-struggle (= the visible stutter) at every
  severity. `stuck`/snap does not.
- The drift is **graphics-path dependent** (software vs hardware decode/render); it does not
  reproduce on the powerful Mac's hardware path but appears with software decode + real VBR
  content, and the VM is too weak to hit the *subtle* 60 fps regime (only total overload).

### Revised detection (replaces Component 1's socket signal)

- **Signal:** the relay polls obs-ws `GetStats` on its heartbeat and tracks
  `renderSkippedFrames` + `renderTotalFrames` (global OBS render stats). The **render-skip
  rate per interval** = `Δrenderskipped / Δrendertotal` (guard `Δtotal > 0`) — a
  resolution/fps-independent fraction, ~0 when healthy.
- **Pure decision** (`tests/test_fanout.py`):
  `render_drift_decision(skip_rate, since_last_reset_s, *, rate_threshold, cooldown_s) -> bool`
  — True when `skip_rate > rate_threshold` **for N consecutive polls (debounce)** AND the
  cooldown has elapsed. Pure, unit-tested.
- **Action (unchanged):** on True with a feed on-air, resync the **on-air feed**
  (`live_feed()` → its port → the proven `_obs_reconnect()` / `release_feed_inputs`),
  cooldown-gated, logged. `GetStats` is global, so a render-skip spike during a single
  on-air feed is attributed to that feed (documented simplification; the reset is cheap +
  cooldown-gated).
- **obs-ws counts (as built):** rather than a second GetStats round-trip, the raw
  `renderSkippedFrames` / `renderTotalFrames` are threaded through the EXISTING per-heartbeat
  probe — `parse_obs_stats` carries them (redaction-safe) into `self.obs_stats`, and the relay
  derives the delta rate from successive samples. One probe, no new obs-ws helper.

### What changes vs what stays

- **Replace:** the socket `stuck_s`/snap sampler in `_serve_fanout` is **removed** (proven
  blind → dead complexity on the live-critical path). `autoresync_decision`'s cooldown logic
  is reused/renamed for `render_drift_decision`. `snap_bytes` / `FeedFanoutServer.consumer_health`
  stay only for the **soak harness** logging (not a relay trigger).
- **Keep:** the graduated stall watchdog (Component 2, trigger B — real fix), the ring
  headroom (16 MB), the config plumbing, and the soak harness (now with `--source` /
  `--quality` for real-stream soaks).
- **Config:** keep `RACECAST_FEED_AUTORESYNC` (kill-switch, default on) +
  `RACECAST_FEED_AUTORESYNC_COOLDOWN_S`; **replace** `RACECAST_FEED_AUTORESYNC_STUCK_S`
  with `RACECAST_FEED_AUTORESYNC_SKIP_RATE` (render-skip-rate threshold, start **0.02** =
  2 %, soak-tuned). The debounce is a code constant `AUTORESYNC_DEBOUNCE_POLLS = 2` (YAGNI —
  not an env knob).
- **Health-DB (added):** health_store **v7** column `obs_render_skip_rate_pct` (the per-interval
  rate, written each heartbeat) + a Health-Monitor uPlot series — so the drift is finally
  *visible* and the next real box event validates the 2 % threshold from recorded data.

### Tuning / validation — on the Mac (no VM, no box)

The Mac can **induce render-skip misses** (the 4K60-canvas + CPU-load path already did:
render-lag misses climbed there) while `GetStats` reports the rate. Tune
`RACECAST_FEED_AUTORESYNC_SKIP_RATE` so a healthy stream stays below it (no false resync)
and an induced render-skip spike crosses it (resync fires). The subtle box-exact threshold
is confirmed at the next real event by the instrumentation; the mechanism is validated on
the Mac. The socket `stuck`/snap findings and the box's 0.2 % render-skip peak are the
priors.

_(This pivot supersedes Component 1's socket detection and the backpressure question — the
drift is a render-path phenomenon, not a ring-buffer/backpressure one, so no backpressure is
built.)_

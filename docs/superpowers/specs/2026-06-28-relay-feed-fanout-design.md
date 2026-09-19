# Relay-side feed fan-out — design

**Issue:** #358 (split off from the Director Panel live-preview redesign,
`docs/superpowers/specs/2026-06-28-director-panel-live-preview-redesign-design.md`).

**Status:** design approved, ready for an implementation plan.

## Problem

Today each feed is served by exactly one `streamlink --player-external-http`
process that binds the feed's loopback port and serves **one** consumer — OBS —
directly. Two problems both stem from that single-consumer constraint:

1. **~2 s stale-frame-on-activation glitch (observed live).** The OBS media
   sources keep `close_when_inactive=False`, so OBS holds an open connection to
   the relay's single-consumer streamlink port while the source is off-air.
   Bytes back up in that connection; on activation OBS drains the backlog —
   showing ~2 s of *stale* video before it reaches live.
2. **Preview needs a second pull.** Because streamlink serves a single consumer
   (OBS), the relay cannot read a second live copy of a feed for the Director
   Panel preview. The #359 redesign works around this with a decoupled low-res
   second pull (one extra streamlink + an isolated YouTube bot-check) for the
   off-air tile.

A secondary, strategic motivation: the hard coupling of the transport to OBS is
not ideal. Making the relay the transport hub — with OBS one interchangeable
consumer among others — keeps the relay platform-independent and makes OBS
replaceable later.

## Approach: the relay becomes the fan-out hub

The relay becomes the single persistent consumer of each feed's streamlink and
re-serves the stream to multiple consumers (OBS **and** the preview):

```
              ┌─────────────── relay process ───────────────┐
YouTube/Twitch│  streamlink --stdout ─► ring buffer ─► TS HTTP server (port 53001/2/3, 127.0.0.1 only)
   (1 stint)  │      (live reader,        (most-recent   │        ├─► OBS  (connects / disconnects freely)
              │     never blocks)           bytes)        │        └─► preview (internal consumer)
              └──────────────────────────────────────────────┘
```

Consequences:

- The relay always reads at the live edge → on activation OBS gets fresh frames
  (kills the ~2 s stale catch-up); the off-air feed stays genuinely warm.
- The preview taps the relay's ring buffer → **no second pull, no extra
  bot-check, no OBS-port contention**.
- With `close_when_inactive=True` OBS no longer pins the port while off-air; the
  relay keeps the feed warm regardless of which consumers are attached.

### Decisions taken during brainstorming

- **Rollout: coexistence + switch.** The fan-out is built **alongside** the
  current direct-serve path and selected by a machine-level flag
  (`RACECAST_FEED_FANOUT`, default **off**). A producer can fall back to the
  proven path instantly if anything misbehaves on event day. The flag (and the
  old path) can be retired after several green events. This directly addresses
  the core risk: the relay is the heart of the system, and a regression here
  must never strand a producer with no working transport.
- **Transport: raw opaque byte-tee, minimal latency.** The relay passes
  streamlink's bytes through 1:1; a joining consumer starts at the live edge and
  OBS's own ffmpeg demuxer resyncs forward. That holds for MPEG-TS, which
  carries its own sync byte. It does **not** hold for fMP4/CMAF, which some Twitch
  channels serve: the codec parameters live once, in the stream's `moov`, and a
  mid-stream join lands inside an `mdat` that ffmpeg refuses to open (#577, R2
  below). So the tee is opaque for TS and repairs the join for fMP4 only. It adds
  the least latency. We accept a small join-cleanliness risk on TS (see the
  reserve lever below) in exchange for low latency.

### Why the raw tee still fixes the glitch

The actual bug is not "OBS needs a keyframe" — it is "OBS holds a connection in
which bytes *back up* while off-air and must replay that backlog on activation."
With `close_when_inactive=True` OBS is **disconnected** while off-air, so there
is no backlog. The relay reads at the live edge throughout. On activation OBS
opens a fresh connection, the relay serves from "now", and OBS's ffmpeg
forward-syncs to the next keyframe (≤ ~1 GOP) showing **live** frames. We trade
"2 s *stale*" for "≤ ~1 s to live"; the backlog is gone. The only residual
unknown is how clean the forward-sync looks, covered by the reserve lever and
the live-UAT gate.

## Components

### 1. The fan-out hub (per feed)

**Ring buffer.** A bounded byte ring holding the most recent few seconds of the
feed at its bitrate (size-capped at a few MB — never unbounded). The live-reader
thread reads `streamlink --stdout` in blocks and always appends; when the ring
is full it overwrites the oldest bytes. **The live reader waits for no one.**

**Consumers with their own cursor.** Each HTTP consumer (OBS, preview) has its
own read cursor into the ring and its own handler thread. New bytes wake waiting
consumers via a condition variable. One ring feeds many readers with no second
pull.

**Slow-consumer policy = drop, never stall.** Two protective layers:

1. If a cursor falls so far behind that the live reader would overwrite it, the
   cursor is **snapped forward to the live edge**. The slow consumer loses a few
   bytes (ffmpeg resyncs); the reader is never throttled.
2. If OBS's socket blocks on send (OBS hung), only **that one** handler thread
   blocks — not the reader and not the other consumers. If it stays blocked too
   long the connection is **dropped** (OBS reconnects on its own). The on-air
   feed is unaffected.

**Join model (v1, opaque).** A joining consumer starts at the live edge; bytes
pass through 1:1; OBS's ffmpeg forward-syncs. That is enough for MPEG-TS.

**fMP4 join repair (#576, #577).** `FeedRing` keeps the first bytes of the
current upstream process (`head()`, reset when the writer starts a new one). A
consumer whose head is fMP4 is sent the initialization segment (`ftyp`…`moov`)
first, and its read is held until the first `moof` box, so the demuxer starts at a
fragment boundary. `Fmp4Join` does this for every mid-stream ring consumer: the
OBS serve, the Director-Panel preview tap and the program-audio tap. A TS head
yields no init segment and the bytes pass through unchanged. A cursor snap after
the join is not re-aligned: the demuxer is mid-`mdat` by its own count, and the
heal is the resync rebuild's fresh connection.

**Reserve lever (only if the live join is visually too rough).** A *light* TS
alignment: scan 188-byte packets and start the consumer at the most recent
PAT/PMT so the demuxer has program info immediately. This is packet-header
scanning, **not** codec demux — far less fragile than a full parser, and stays
TS-specific (Twitch fMP4 falls back to opaque). **Not** part of v1; added only
if the live-UAT shows v1 is insufficient.

**Implementation shape.** Per feed: 1 reader thread + N handler threads, one
size-capped ring, condition-variable wakeups. Pure stdlib (`socket` /
`threading`), consistent with the dependency-light relay. The pure ring logic
(append, cursor-snap, drop decision) is a standalone, unit-testable module with
no real stream.

**Binding.** The relay binds the feed ports (53001/53002/53003) itself, in
addition to its control port 8088. Feed ports stay **`127.0.0.1`-only** — OBS is
local; unlike `/panel` and `/hud`, the feed ports are never bound to the
Tailscale IP. The trust boundary is unchanged.

### 2. Health / DROP, re-expressed (R4)

Today the drop signal is "the streamlink serve process exited unexpectedly"
(`serve_exit_is_drop`), and the whole #278 escalation (`dropped`,
`dropped_since`, the 30 s grace, `served_ok`, `dead_serves`, backoff,
idle-after-N) hangs off it. With fan-out the reader runs **persistently** across
swaps, so the signal moves. Three changes; everything else stays.

1. **Drop trigger = upstream, not process exit.** The live reader stamps
   `last_byte_ts` on every block. "Healthy" now means **bytes are flowing.** Two
   loss cases:
   - **streamlink ends** (EOF — 403, expired manifest, stream over) → handled as
     today: the loop re-resolves; `dead_serves` / backoff / idle-after-N apply
     unchanged.
   - **byte-stall** (streamlink alive but no bytes for `> STALL_S`) → **new.** A
     watchdog kills the stalled streamlink and treats it as drop + restart.
     Today a stalled-but-alive streamlink would hang silently (a latent bug);
     fan-out improves on this.
2. **Consumer presence is no longer a health signal.** This is the key
   conceptual point: with `close_when_inactive=True` OBS connects/disconnects
   **constantly** (every off-air moment = disconnected). That must **never**
   count as a feed problem. Feed health henceforth measures **only** the upstream
   (streamlink→relay); how many consumers are attached is health-irrelevant. A
   cleaner separation than today.
3. **The tested classification logic stays — only its inputs change.**
   `serve_exit_is_drop`, `should_idle_dead_serves`, `dead_serve_backoff`,
   `feed_fast_exit_error`, `health_should_notify`, the 30 s grace, `served_ok`
   stickiness: all remain pure functions, now fed by "EOF/stall" instead of
   "serve exit". The `Feed.run` structure largely survives: "`Popen` serve +
   `proc.wait()`" becomes "`Popen --stdout` + pump-into-ring loop + detect
   EOF/stall". Resolve, cookies, Twitch token, and `phase` transitions are
   unchanged.

**Positive side effect:** streamlink retries HLS segments **internally**; a brief
network hiccup that surfaces today as a serve-exit→drop will not surface at all
under fan-out. Fewer false alarms — only the hard stall (watchdog) remains a real
drop. `/status` and the panel/Companion alarm keep today's semantics
(`feeds.<X>.down`, CRITICAL after grace), just fed more robustly. In coexistence
mode (flag off) the old process-based logic runs unchanged.

### 3. OBS integration & the switch

**`close_when_inactive=True` is mandatory for the glitch fix and only safe with
fan-out.** The relay alone cannot fix the glitch: as long as OBS stays connected
off-air it keeps buffering the sent bytes → backlog → glitch. The fix requires
OBS to **disconnect** off-air and reconnect fresh on activation. In today's
direct-serve that flag would be *dangerous* (single-consumer streamlink can exit
on disconnect → re-resolve per swap, the 403 risk from the
`youtube-feed-403-streamlink-context` memory note). The flag is therefore **tied
to the fan-out mode**.

**Coupling mechanism: live via obs-websocket, not via re-import.** Rather than
baking the flag into the OBS collection (which would force a `racecast setup` +
re-import on every mode change), the relay sets it **live on the feed media
inputs at start** — the pattern that already exists (`_sync_pov_transform`,
`_release_obs_feeds` manipulate the feed sources best-effort via `obs_ws` on
start/stop):

- fan-out **on** → relay sets `close_when_inactive=True` on the feed inputs.
- fan-out **off** → relay ensures `False` (today's behaviour).

The flag stays the **single source of truth**, applied best-effort; the operator
touches nothing in OBS and re-imports nothing. If obs-ws fails, a notice is
logged and the feed still runs (only the glitch fix does not take effect — like
every other obs-ws hook).

**The switch.** `RACECAST_FEED_FANOUT=1` in the machine `.env` — transport is a
**machine concern, not a league concern** (it does not belong in `profile.env`).
The relay reads it at start. Default **off** → existing behaviour, no surprises.
Optionally mirrored as a Control Center setting (General Settings, beside the
other `RACECAST_*` knobs); that mirror is a nice-to-have, not required.

**Preview becomes "free".** With fan-out on, `PreviewManager` sources the off-air
tile as an **internal consumer of the hub** instead of the #359 second pull — no
second streamlink, no extra bot-check. The `still(target)` abstraction from #359
stays; a hub-backed source is selected when the flag is on, the second pull when
it is off. The audio meter continues via an ffmpeg/ebur128 over the hub tap
(same pure parsers as today).

**Stop path gets cleaner.** Today's `_release_obs_feeds` manoeuvre (OBS pins the
streamlink port in FIN_WAIT_1, preflight warns "port in use") largely disappears
under fan-out: the relay owns the port; on relay stop it closes its server and
OBS's transient connection simply drops. In coexistence mode (flag off)
`_release_obs_feeds` is unchanged.

## Testing

Two layers — pure units for the logic (run in CI on all three OSes), live-UAT for
what only a real stream reveals.

**Pure unit tests (stdlib runnable scripts — new `tests/test_fanout.py`):**

- **Ring buffer:** append, size cap, cursor-snap to live edge on overflow, drop
  decision. The "reader never blocks" invariant is tested **deterministically**:
  a fake producer fills the ring, two loopback-socket clients read, an
  artificially slow client is snapped — without throttling the producer. No real
  streamlink.
- **New health:** byte-stall watchdog decision (`now - last_byte_ts > STALL_S`),
  EOF→`dead_serves` mapping. The existing pure functions
  (`serve_exit_is_drop`, `should_idle_dead_serves`, `dead_serve_backoff`,
  `feed_fast_exit_error`, `health_should_notify`) get **new input cases** in
  `tests/test_pov.py` — the functions themselves stay.
- **Switch & preview routing:** `RACECAST_FEED_FANOUT` parsing; `PreviewManager`
  picks the hub source when the flag is on, the second pull when off.
- **obs-ws:** the `close_when_inactive` set call as a builder test (like the
  existing `tests/test_obsws.py`).

**e2e harness (`tools/e2e.py` synthetic, CI):** in fan-out mode, assert the relay
binds the feed ports **itself** and the endpoints stand. The synthetic mode
cannot exercise real TS throughput (the no-op stubs stream no TS) — that is
explicitly the live-UAT's job.

**Live-UAT gate (blocking before any ship — `racecast-local-uat` /
`racecast-e2e`):** verify against **real YouTube AND Twitch** streams:

1. OBS shows **live, not stale**, within < 1 s of activation (the actual glitch
   proof).
2. The off-air feed stays warm — no re-resolve per swap, no false DROP.
3. The preview reads with no second pull / extra resolve.
4. A full swap with no regression to the on-air feed.
5. A slow consumer does not stall the live path.

**Rollout discipline (the coexistence payoff):** ship behind the flag, default
**off**; live-UAT on a real rehearsal / event dry run; flip the default — or
retire the old path — only after several green events. The change to the heart of
the system can then never strand a producer on event day: in case of doubt they
turn one flag back.

## Risk summary

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | TS passthrough to multiple consumers; a joining client must decode from a live-edge start | High | Ring with cursor-snap; live reader never blocks; opaque v1 + light-TS-alignment reserve lever |
| R2 | Container variance YouTube (MPEG-TS) vs Twitch (fMP4/CMAF) | High | The opaque pass-through is NOT format-agnostic: a mid-stream fMP4 join does not open (measured, #577). Fixed by the fMP4 join repair above; verified through the real serve with ffmpeg as the OBS stand-in |
| R3 | OBS reconnect with `close_when_inactive=True` — the glitch fix depends on it | Medium | Set live via obs-ws (reversible); proven by live-UAT |
| R4 | Health/DROP logic must move off "process exit" | Medium | Pure functions reused with new inputs; add byte-stall watchdog; consumer presence decoupled from health |
| R5 | Extra-hop latency (streamlink→relay→OBS) | Low | Commentator feeds are not frame-accurate; small buffering is fine |
| R6 | Cross-platform serving (Win/Mac/Linux) | Low | Pure stdlib socket serving |
| R7 | Regression to the live-critical path strands a producer | High (product) | Coexistence + `RACECAST_FEED_FANOUT` fallback switch; live-UAT gate; default off until proven |

## Out of scope

- Retiring the direct-serve path or flipping the default to on — a later decision
  after the fan-out has proven itself across real events.
- The light-TS-alignment reserve lever — implemented only if the opaque v1 join
  is shown insufficient by the live-UAT.
- Any change to the Tailscale/Funnel trust boundary — feed ports stay
  loopback-only, exactly as today.

## References

- Issue #358.
- Preview redesign spec:
  `docs/superpowers/specs/2026-06-28-director-panel-live-preview-redesign-design.md`.
- Relay feed loop: `src/relay/racecast-feeds.py` (`Feed.run`,
  `streamlink_serve_cmd`, the `_PreviewPullWorker` precedent for reading
  `streamlink --stdout` in-process).
- OBS connection behaviour and the live-set pattern: `src/scripts/obs_ws.py`
  (`_sync_pov_transform`, `_release_obs_feeds`).
- Memory: `youtube-feed-403-streamlink-context` (why streamlink re-resolve is
  delicate), `no-prod-use-prefer-clean-breaks` (racecast is released —
  backward-compat matters, hence the coexistence switch).

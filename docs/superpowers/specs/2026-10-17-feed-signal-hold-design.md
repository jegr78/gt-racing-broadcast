# Feed signal hold and ultra-low-latency hint: design

Date: 2026-10-17. Status: approved in conversation, awaiting spec review.

## Problem

During the Suzuka 6h race (2026-10-17) both remote YouTube feeds stalled at the source:

- Feed B (stint 2, a stream sent directly from a PlayStation): inbound stalls at 17:10 (5m 43s yellow), 17:54, 18:10, 18:37.
- Feed A (stint 3): stalls at 19:01, 20:21-20:26, 20:31, 20:42 and 21:04-21:07, with inbound gaps up to 5.7 s and OBS audio re-syncs of up to 1.4 s. Streamlink logged HLS `Sequence gap` warnings at 20:21:34 and 20:31:02: YouTube dropped segments before we could fetch them.
- The producer had to reload Feed A by hand at 20:03:53 because the stutter was no longer acceptable on air. The reload did not help: the cause was upstream.

Feed A's playlist had **1 s segments**. Streamlink positions 5856 (20:21:34) and 6425 (20:31:02) are 569 segments in 568 s. That is YouTube's ultra-low-latency mode (normal latency measured `#EXT-X-TARGETDURATION` 5 on 2026-09-21). The commentator confirmed ultra-low latency was enabled.

The producer host was not the cause: 0 % dropped frames, a steady ~6.2 Mbit/s upload, and Feed A inbound only 1-2 Mbit/s during the stall against 7-9 Mbit/s normally, with a 10 ms ping.

### What the relay does today

OBS is served up to a trailing mark `RACECAST_FEED_PREBUFFER_S` (3 s) behind the newest byte in the fan-out ring. That reserve is the same for every source, whatever `--hls-live-edge` prefetches. An inbound gap longer than the reserve drains it. OBS then gets the stream in bursts, which shows as stutter with audio re-syncs. The `robust` tier (720p, live edge 6) steps in automatically only after two consecutive dead serves, so stalls without a restart never trigger it.

## Goals

1. Viewers never watch a stuttering feed. When a feed cannot keep the picture smooth, they see a clean frozen frame with the sound muted and a short notice, and the live picture returns when the source is steady again.
2. The producer and director learn **before** a stint goes on air that a commentator streams in ultra-low latency, so it can be fixed at the source. The crew setting the producer wants is YouTube "Low latency": normal latency adds too much offset, ultra-low is too fragile. Whether that becomes a crew rule is the team's decision; the relay only reports what it sees.

## Non-goals

- **No extra delay and no quality step-down to absorb gaps.** The producer decided this in #619 (2026-10-03) and confirmed it again for this design. The 3 s reserve, the live-edge counts and the tier logic stay as they are.
- No signal hold in the Splitscreen scene in this version (two feeds on air at once). Stint scene only.
- No change to the 20 s stall watchdog, the drop/recovery path or the backlog shed, beyond coordinating with them (see "Interactions").

## Part A: ultra-low-latency hint

**Detection.** When a remote HLS stint starts serving (YouTube or Twitch), the relay fetches the media playlist once and reads `#EXT-X-TARGETDURATION`. The resolved URL from `yt-dlp -g` is the HLS manifest; a master playlist is followed to its first variant. Both are plain HTTP GETs with the relay's own User-Agent (the relay is a self-contained script and keeps its own HTTP code, per the root CLAUDE.md exception). The value is stored per serve and published read-only in `/status` as `feeds.<X>.target_duration_s` (`null` for local capture, a failed fetch or no tag). A fetch failure never blocks or delays the serve. It is a background read with a short timeout.

**Threshold.** `ULL_TARGET_DURATION_S = 1`: a feed with `target_duration_s <= 1` is flagged `ultra_low_latency: true` in `/status`. Before implementation, measure what YouTube "Low latency" reports with `tools/prefetch-burst-probe.py` or a one-off playlist fetch against a real Low-latency test stream (expected ~2 s). Record the value in this spec and in the constant's comment. If Low latency also reports 1 s, the hint cannot tell the two apart. Stop and come back to the producer then.

**Panel.** The Director Panel's feed card shows a yellow badge `ULTRA-LOW LATENCY` with the hint text "Ask the commentator to switch to Low latency". It shows on the off-air feed too, because ARM serves the next stint minutes before the handover, which is exactly when there is time to ask. The badge is informational: no paging and no health level change.

## Part B: signal hold

### State machine (per on-air feed)

`LIVE -> HOLD -> LIVE`, decided by one pure function so the rules are unit-testable:

```
signal_hold_step(state, now, *, reserve_s, last_byte_age_s, steady_s,
                 engage_idle_s=1.0, release_steady_s=5.0) -> (state, action)
```

- **Engage** (`LIVE -> HOLD`): the feed is the on-air feed in the Stint scene, an OBS consumer is attached, the reserve is used up (`reserve_s <= RESERVE_EMPTY_S`, 0.25 s) **and** no byte has arrived for `engage_idle_s` (1.0 s). Gaps the 3 s reserve absorbs never engage.
- **Release** (`HOLD -> LIVE`): bytes have arrived steadily for `release_steady_s` (5.0 s). Steady means no inbound gap above `STEADY_GAP_S` (1.5 s) inside that window. A single burst after a stall therefore does not release, which avoids flicker.
- **Cancel** (`HOLD -> LIVE` without a rejoin): the stint changes (handover, `/next`, ARM of the on-air feed), the director presses RESET (`/obs/feed-reset`), the scene leaves Stint, or the feature is switched off. The existing action of each trigger then owns the OBS input.

`reserve_s` is the ring's `consumer_backlog` (seconds between OBS's read position and the newest byte). `last_byte_age_s` comes from `last_byte_ts`. `steady_s` is tracked by the monitor from the byte arrivals.

**Monitor.** A relay thread evaluates the function once per second (`SIGNAL_HOLD_TICK_S = 1.0`). The 30 s health heartbeat is far too coarse for this. The thread only reads ring state and calls the actions below. It never blocks the serve loop.

The defaults reflect the producer's choice ("engage when the reserve is empty and 1 s without data, release after 5 s steady"). They are module constants first. Tune them against the 2026-10-17 health history and feed logs before release, and expose them as env knobs only if tuning shows that one value does not fit all leagues.

### Actions

**On engage:**

1. **Freeze.** The ring stops advancing OBS's serve cap: `FeedRing` gets a `hold()` that pins the trailing mark at OBS's current read position, so OBS receives no more bytes and keeps showing its last decoded frame (`is_clear_on_media_end: no`, set in the collection). Bytes keep flowing into the ring from streamlink, so recovery is measured on real data.
2. **Mute.** `obs_ws.get_input_mute` remembers the feed input's current state, then `set_input_mute(<Feed X>, True)`. Best effort: when OBS is unreachable, the freeze still applies.
3. **Notice.** `/hud/data` `raceControl` is overridden in the relay with the Signal Hold text (see below). No Sheet write, because a Sheet push takes 10-20 s and is unreliable on event nights (repeated timeouts on 2026-10-16/17). The Sheet's own Race Control value stays untouched and is shown again after the hold. The HUD polls `/hud/data` every 2.5 s, so the notice appears within that time. That is acceptable and needs no HUD change.
4. **Record.** A `signal_hold` event goes to the health store with the feed, the stint and the start time.

**On release:**

1. **Rejoin at the live edge.** `_obs_reconnect()` rebuilds the feed's OBS input, which rejoins `RACECAST_FEED_PREBUFFER_S` behind the newest byte. The time spent in the hold is skipped, not replayed, so the offset after the hold is the same as before it. The rebuild runs through `RebuildGuard` with the reason tag `signal_hold` and registers a splice via `_note_obs_splice`, so the A/V detector does not count the relay's own remedy as unexplained.
2. **Unmute** only if engage muted it (the remembered state), so a director's own mute survives.
3. **Clear the notice override**, so the HUD shows the Sheet's Race Control value again.
4. **Record** the end time and duration on the `signal_hold` event.

**On cancel:** remove the ring hold, restore the mute state and clear the override. No rejoin of its own.

### Notice text (Sheet)

The Configuration tab gets a new column `Signal Hold`, located by header like the other Configuration columns (`parse_config_vocab` precedent, case-insensitive). The first non-blank value is the text; the team words it. When the column is missing or empty, the hold shows no text (freeze and mute only). The relay does not invent a default notice. The value is read on the normal Sheet poll and cached like the Race Control vocabulary. The text is shown in the HUD's existing `#race-control` slot, so it uses the league's overlay styling like "Driver Swaps" does.

### Interactions with existing automation

- **Stall watchdog (20 s).** A source silent for 20 s is killed and restarted by the existing path. The hold stays engaged across the restart, because the freeze and the notice remain the clean picture. It releases only once the new serve has delivered `release_steady_s` steadily. The release rejoin replaces the recovery path's own rejoin (one rebuild, not two).
- **Backlog shed and freeze detector.** They share `RebuildGuard`. While a hold is engaged, the shed and the freeze detector stand down for that feed, because a held feed is not behind live by design.
- **A/V re-sync detector.** The hold's freeze and rejoin register as splices, so they do not add "unexplained" audio re-syncs.
- **Feature switch.** `RACECAST_FEED_SIGNAL_HOLD` in the machine `.env`, default on, `0` disables it. Switching it off while a hold is engaged cancels it.

### Visibility

- `/status` `feeds.<X>.signal_hold`: `{active, since, stint}`.
- Director Panel: a `SIGNAL HOLD` pill on the feed card and in the header while engaged, next to `BEHIND LIVE`.
- Post-event report: a section "Signal holds" with the time, feed, stint, commentator and duration of each hold. Hold time is excluded from the "behind live" finding. It stays inside the incident window that the stall itself raises.

## Testing

- **Unit (pure).** `signal_hold_step` gets a table of cases: a short gap absorbed by the reserve (no engage), the reserve empty plus 1 s idle (engage), a single burst after a stall (no release), 5 s steady (release), a cancel on handover, RESET and scene change, and the feature switched off mid-hold. Cases are replayed from the 2026-10-17 readings (inbound gaps 1.67 -> 5.72 s, backlog floor 0.8 s).
- **Unit.** `FeedRing.hold()` pins the serve cap while writes continue. Parsing `#EXT-X-TARGETDURATION` from master and media playlists, and a failed fetch returning `None`. The `Signal Hold` column parse (missing, empty, value). The `/hud/data` override and restore.
- **e2e.** Extend the synthetic relay harness: a local feed source pauses for 6 s and then resumes. Assert that the HUD race-control slot shows the configured text, that obs-sim receives the mute and later the unmute plus one rebuild, and that `/status` shows the hold.
- **Manual, real OBS, not live.** The open risk below.

## Risks and open points

1. **Black flash on release.** Rebuilding the media input may show black for about 1 s (reconnects on 2026-10-16 took ~1 s) instead of holding the frame. Test it in the producer's OBS before release. If it flashes, fall back to grabbing a frame of the feed source when the hold engages (`GetSourceScreenshot`) and showing it above the feed until the first new frame arrives.
2. **Low-latency target duration** must be measured before Part A's threshold is final (see Part A).
3. **HUD notice latency** of up to 2.5 s (HUD poll). Accepted for now. A faster poll is out of scope.
4. **Splitscreen** has no hold in this version. If both feeds stall in Splitscreen, the existing behaviour stays.

## Rollout

Ship Part A and Part B as separate PRs (Part A is small and independent). Part B is on by default, with the env switch as the instant fallback. Update the wiki operator pages (Director Panel, Troubleshooting) and the relay `CLAUDE.md` alongside the code. The Panel badge and pill need a refreshed `director-panel.png`.

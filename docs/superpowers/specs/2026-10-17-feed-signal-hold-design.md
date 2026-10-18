# Feed covers, signal hold and ultra-low-latency hint: design

Date: 2026-10-17. Status: approved in conversation (revision 2: covers replace the byte hold, Splitscreen included; Part C rule revised after the live OBS test 2026-10-18).

## Problem

During the Suzuka 6h race (2026-10-17) both remote YouTube feeds stalled at the source:

- Feed B (stint 2, a stream sent directly from a PlayStation): inbound stalls at 17:10 (5m 43s yellow), 17:54, 18:10, 18:37.
- Feed A (stint 3): stalls at 19:01, 20:21-20:26, 20:31, 20:42 and 21:04-21:07, with inbound gaps up to 5.7 s and OBS audio re-syncs of up to 1.4 s. Streamlink logged HLS `Sequence gap` warnings at 20:21:34 and 20:31:02: YouTube dropped segments before we could fetch them.
- The producer had to reload Feed A by hand at 20:03:53 because the stutter was no longer acceptable on air. The reload did not help: the cause was upstream.

Feed A's playlist had **1 s segments**. Streamlink positions 5856 (20:21:34) and 6425 (20:31:02) are 569 segments in 568 s. That is YouTube's ultra-low-latency mode (normal latency measured `#EXT-X-TARGETDURATION` 5 on 2026-09-21). The commentator confirmed ultra-low latency was enabled.

The producer host was not the cause: 0 % dropped frames, a steady ~6.2 Mbit/s upload, and Feed A inbound only 1-2 Mbit/s during the stall against 7-9 Mbit/s normally, with a 10 ms ping.

A second, older defect has the same cure. **Every time OBS (re)opens a feed source it shows black** until its demuxer has the first decoded frame. This happens in three cases:

- the Splitscreen at every handover: `close_when_inactive` reopens the incoming feed;
- every automatic OBS input rebuild: the post-event report itself calls each one "a short black dropout";
- the director's RESET.

### What the relay does today

OBS is served up to a trailing mark `RACECAST_FEED_PREBUFFER_S` (3 s) behind the newest byte in the fan-out ring. That reserve is the same for every source, whatever `--hls-live-edge` prefetches. An inbound gap longer than the reserve drains it. OBS then gets the stream in bursts, which shows as stutter with audio re-syncs. The `robust` tier (720p, live edge 6) steps in automatically only after two consecutive dead serves, so stalls without a restart never trigger it.

## Goals

1. **No black when a feed source (re)opens.** Viewers see a current still of that feed until OBS plays it.
2. **No stutter on air.** When a feed cannot keep the picture smooth, viewers see a frozen still of it, with the sound muted and a short notice from the Sheet, and the live picture returns when the source is steady again.
3. **Know about ultra-low latency before air.** The producer and director learn **before** a stint goes on air that a commentator streams in ultra-low latency, so it can be fixed at the source. The crew setting the producer wants is YouTube "Low latency": normal latency adds too much offset, ultra-low is too fragile. Whether that becomes a crew rule is the team's decision; the relay only reports what it sees.

## Non-goals

- **No extra delay and no quality step-down to absorb gaps.** The producer decided this in #619 (2026-10-03) and confirmed it again for this design. The 3 s reserve, the live-edge counts and the tier logic stay as they are.
- No cover or hold for the POV feed.
- No signal hold for a local capture stint. Its reserve is 0.5 s and the commentary mic is part of its audio. Local stints still get covers.
- No change to the 20 s stall watchdog, the drop/recovery path or the backlog shed, beyond coordinating with them (see "Interactions").

## Part A: ultra-low-latency hint

**Detection.** When a YouTube stint starts serving, the relay fetches the media playlist once and reads `#EXT-X-TARGETDURATION`. The resolved URL from `yt-dlp -g` is the HLS manifest; a master playlist is followed to its first variant. Both are plain HTTP GETs with the relay's own User-Agent (the relay is a self-contained script and keeps its own HTTP code, per the root CLAUDE.md exception). Twitch is served by token through streamlink, with no manifest URL in the relay, so it keeps `null`. The value is stored per serve and published read-only in `/status` as `feeds.<X>.target_duration_s` (`null` for local capture, a failed fetch or no tag). A fetch failure never blocks or delays the serve. It is a background read with a short timeout.

**Threshold.** Measured 2026-10-18 on a live YouTube "Low latency" stream: `#EXT-X-TARGETDURATION:2` (2.0 s segments, video and audio playlists). `ULL_TARGET_DURATION_S = 1`: a feed with `target_duration_s <= 1` is flagged `ultra_low_latency: true` in `/status`. Before implementation, measure what YouTube "Low latency" reports with a one-off playlist fetch against a real Low-latency test stream (expected ~2 s). Record the value in this spec and in the constant's comment. If Low latency also reports 1 s, the hint cannot tell the two apart. Stop and come back to the producer then.

**Panel.** The Director Panel's feed pill for that feed turns amber and gets the suffix `ULL`, with the hint "Ultra-low latency: ask the commentator to switch to Low latency" as its tooltip. It shows on the off-air feed too, because ARM serves the next stint minutes before the handover, which is exactly when there is time to ask. The hint is informational: no paging and no health level change.

## Part C: feed covers (shared by the Splitscreen and the signal hold)

**OBS sources.** For each feed there is one image input, `Feed A Cover` and `Feed B Cover`. Each one is added as a scene item directly above its feed's item in the `Stint` and `Splitscreen` scenes, with the same transform. The relay creates these on start when they are missing: `CreateInput` in the first scene, `CreateSceneItem` in the second, then index directly above the feed item and copy the feed item's transform. Existing installs therefore need no collection re-import. A scene without the feed item gets no cover. The input's `file` is `runtime/<profile>/covers/feed-<x>.jpg` (an absolute runtime path, like the localized import's paths).

**The still.** A `FeedCoverTap` per fan-out feed reads the feed's ring from the live edge into one ffmpeg process that decodes **keyframes only** (`-skip_frame nokey`, about one every 2 s on YouTube, so the CPU cost is low). It writes each 1920-wide JPEG atomically to `covers/feed-<x>.latest.jpg`. OBS shows a different file, `covers/feed-<x>.jpg`, because OBS reloads an image source by itself whenever its file changes; a tap writing the shown file made the visible still flicker every ~2 s (live test 2026-10-18). The relay copies latest over shown (`_snapshot_cover`) once when the cover is raised on the program scene, and every `COVER_REFRESH_S` (5 s) only while the cover is not visible on the program. Viewers therefore always see a frozen still.

**Visibility rule.** Decided by one pure function per feed, evaluated every second:

```
cover_visible(*, enabled, serving, obs_playing_s, hold_active, feed_item_shown) -> bool
```

The cover is **shown** unless OBS demonstrably plays the feed: its `GetMediaInputStatus` mediaCursor has advanced on every sample for `COVER_PLAY_S` (1.0 s), and no signal hold is active. Bytes reaching OBS are not proof. YouTube's 5 s segments reach OBS in bursts, and the picture waits for a keyframe; both were measured live, where a byte-based rule first never lowered the cover and then lowered it onto black. The cover stays up otherwise, so it is already in place **before** OBS opens the source, including scene switches made by hand in OBS.

**A cover item is never shown while its feed item is hidden in that scene.** In the `Stint` scene the off-air feed's item is hidden, and a cover above it would still show over the on-air feed. The tick reads the feed item's enabled state (`GetSceneItemEnabled`), and pre-shows target only scenes where the feed item is visible. The relay applies a state only on change, and re-asserts it every 30 s to heal a lost request. It raises the cover ahead of OBS at three moments:

- the three cut sites (Splitscreen, stint pick, the handover reflect), unless OBS already plays that feed;
- every input rebuild (`Feed.before_obs_reconnect`, forced: RESET, freeze detector, backlog shed and prefetch rejoin all reopen a feed OBS was playing).

**Switch.** `RACECAST_FEED_COVERS` (machine `.env`, default on, `0` disables and hides any shown cover).

## Part B: signal hold (uses the covers)

### State machine (per on-air remote feed), as live-tested 2026-10-18

`LIVE -> HOLD -> (settle) -> LIVE`, decided by one pure function:

```
signal_hold_step(state, *, starved, obs_stalled_s, last_byte_age_s, steady_s,
                 cancel, held_s, segment_s) -> (state, action)
```

The first design engaged on "reserve empty and 1 s without bytes". Live OBS tests disproved it:

- OBS played a 7 s source stall out of its own buffer, unseen.
- The ring backlog is the age of OBS's read position, so it grows during a stall instead of draining.
- YouTube's 5 s segments make 2-5 s byte gaps normal.

The tested rules:

- **Engage:** OBS has stopped advancing a feed it **had played** for `SIGNAL_HOLD_ENGAGE_IDLE_S` (1 s). Opening or reopening a source is never a stall (`media_play_update`'s `played`). The source must also be the cause, in one of two ways:
  - OBS has read up to the newest byte (`FeedFanoutServer.consumer_starved`);
  - the source has been silent past its own cadence, `source_silent_s(segment) = max(2, 1.5 x segment)`, where segment is `#EXT-X-TARGETDURATION` from Part A, 5 s when unknown.

  OBS freezing on flowing data stays the freeze detector's case.
- **Release:** steady inbound bytes for `release_steady_s(segment) = max(5, 2 x segment)`, with no gap beyond `source_silent_s` (`Feed.steady_gap_s`).
- **Ceiling:** a hold never outlives `HOLD_MAX_S` (60 s). It then releases with a WARNING (`timeout`).
- **Cancel:** a stint change, a stopped feed, a local stint, or RESET. A running hold **survives** OBS dropping its socket and the 20 s watchdog restarting the serve.

### Actions

**On engage:** force the cover up, keeping the still viewers already see. Mute the feed input only if OBS reported it unmuted (`muted_by_us`). Override `/hud/data` `raceControl` with the Signal Hold text (no Sheet write). Record `signal_hold`.

**While held:** the hold owns OBS's rejoin. A serve restart's own prefetch rejoin is deferred (`Feed.defer_rejoin`), because two rejoins made OBS catch up and then resync audibly.

**On release:** rebuild once at the live edge under the cover (`_obs_reconnect`, splice registered, shared cooldown claimed) and record `signal_hold_end`. Then **settle**: cover, mute and notice stay until OBS demonstrably plays for `HOLD_SETTLE_S` (3 s). Playing needs both of these:

- the mediaCursor advancing at 0.8-1.25x real time, because a crawl or a catch-up is not stable;
- the source thumbnail changing (`motion_update`), because the cursor ran on the audio while the picture still stood.

`HOLD_SETTLE_MAX_S` (30 s) bounds the settle. Then all three lift together.

**Live result** (OBS recording, 18 s stall that also triggered the watchdog restart): freeze and silence, then picture and sound back in the same frame, with no freeze of 0.1 s or more and no silence in the 20 s after. The producer's local OBS audio monitoring still played the catch-up audio while the input was muted. The program output was clean, so this was accepted.

### Notice text (Sheet)

The Configuration tab gets a new column `Signal Hold`, located by header like the other Configuration columns (case-insensitive). The first non-blank value is the text; the team words it. When the column is missing or empty, the hold shows no text (cover and mute only). The relay does not invent a default notice. It is read on the normal Sheet poll and shown in the HUD's existing `#race-control` slot, so it uses the league's overlay styling like "Driver Swaps".

### Interactions with existing automation

- **Stall watchdog (20 s).** The source is killed and restarted by the existing path. The hold stays engaged across the restart and releases only once the new serve has delivered `release_steady_s` steadily. The serve's own rejoin (`_obs_rejoin_after_prefetch`) happens under the cover.
- **Backlog shed and freeze detector.** While a hold is engaged they stand down for that feed, because a held feed is not behind live by design.
- **Handover.** A handover cancels a hold on the outgoing feed. The covers then do their normal job for the Splitscreen and Stint switch.
- **Feature switch.** `RACECAST_FEED_SIGNAL_HOLD` (machine `.env`, default on, `0` disables; switching it off cancels an engaged hold). It needs covers: with `RACECAST_FEED_COVERS=0`, the hold does mute and notice only.

### Visibility

- `/status`: `feeds.<X>.signal_hold` `{active, since}` and `feeds.<X>.cover` (`shown` / `hidden` / `missing`).
- Director Panel: a `SIGNAL HOLD` pill in the status strip while engaged. When the OBS sources are missing, the panel shows an OBS note, like other OBS notes.
- Post-event report: a section "Signal holds" with the time, feed, stint, commentator and duration of each hold.

## Testing

- **Unit (pure).**
  - `signal_hold_step` gets a table of cases: a short gap absorbed by the reserve, the reserve empty plus 1 s idle, a burst after a stall, 5 s steady, and each cancel. Cases are replayed from the 2026-10-17 readings.
  - `cover_visible` gets a table of cases.
  - `parse_target_duration` (master and media playlists).
  - `parse_signal_hold_text`.
  - The `cover_ffmpeg_cmd` argv.
- **Unit (classes with fakes).**
  - `media_play_update`/`media_playing_s` over scripted OBS cursor samples.
  - `FeedCoverTap` writing atomically from a fake ffmpeg.
  - `Relay` hold/cover ticks against a fake obs_ws: mute, unmute, enable/disable calls in order, the notice override in `/hud/data`.
- **e2e.** Extend obs-sim to answer `GetInputMute`, `GetSceneItemList` and `GetSceneItemId`, and to record mute and enable calls. CI has no real remote stream to stall, so the hold is driven through the tick methods with injected readings (clock, reserve, byte ages) against obs-sim, and the e2e asserts the `/status` fields, the `/hud/data` override and the recorded OBS calls.
- **Manual, real OBS, not live:**
  1. A Splitscreen handover shows a still, not black.
  2. A RESET shows a still, not black.
  3. A forced hold (kill the feed's network for 6 s) shows the still, the muted audio and the notice, then returns without black.

## Risks and open points

1. **Low-latency target duration** must be measured before Part A's threshold is final.
2. **Cover lag.** The still is up to ~2 s (keyframe interval) plus `COVER_REFRESH_S` old. For a cover lasting about 1.5 s that is invisible. During a long hold it is the last good picture, which is the goal.
3. **OBS image reload.** OBS must reload the image file on `SetInputSettings` with an unchanged path. If a real OBS caches it, write to two alternating file names instead.
4. **HUD notice latency** of up to 2.5 s (HUD poll). Accepted.

## Rollout

Three PRs:

1. Part A (#918, "Part of").
2. Part C covers (own issue; fixes the Splitscreen and RESET black on its own).
3. Part B hold (closes #918).

Covers and hold are on by default, with env switches as the instant fallback. Update the wiki operator pages (Director Panel, Troubleshooting) and `src/relay/CLAUDE.md` alongside the code. The panel pill changes need a refreshed `director-panel.png`.

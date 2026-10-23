# YouTube audience: follow the live broadcast, clearer setup

Date: 2026-10-23 · Surfaces: Control Center YouTube audience dialog
(`src/ui/control-center.html` ~855-873, JS ~3149-3244), `src/scripts/audience_youtube.py`,
`src/scripts/audience_youtube_connection.py`, `src/scripts/audience_connections.py`,
relay wiring in `src/relay/racecast-feeds.py` (`yt_live_video_ids` ~3353,
`BroadcastChatSupervisor` ~3785, audience service construction ~14391), wiki
`src/docs/wiki/HUD-Overlays.md` (YouTube section ~353-412).

## Intent

The producer connects YouTube audience metrics (likes, concurrent viewers,
subscribers) for the HUD/splitscreen/intermission widgets. Today the dialog needs a
concrete **output video ID**, and every live broadcast has a new video ID, so the
producer must paste, verify and activate a new ID before each event. Nothing in the UI
or the wiki says so, and the API-key setup is described in one sentence.

The league's output channel is already configured in the Sheet's **Channel** tab, and
the relay already resolves that channel's current live video(s) every 30 s for the
broadcast chat, via yt-dlp (no API quota). The producer wants the audience connection
to follow that live broadcast automatically.

## Goals

1. **Follow live broadcast mode.** A YouTube audience connection can be set to follow
   the live broadcast of the Sheet **Channel** tab's YouTube channel instead of a fixed
   video:
   - Likes and concurrent viewers come from the channel's current live video.
   - Subscribers come from the same channel (no separate channel field in this mode).
   - The API key is still required (metrics still use the YouTube Data API).
   - When the channel has **two concurrent live videos** (producer-handover overlap),
     the **newer** one counts (producer decision).
   - When the channel has **no live video**, video metrics are unavailable (widgets
     hide them, never show an old video's values); the dialog shows that state.
2. **Fixed video mode stays** as the second mode (e.g. an unlisted test stream).
3. **Clearer setup (docs + dialog):**
   - Wiki: step-by-step API key creation (Google Cloud project, enable YouTube Data
     API v3, Credentials → Create API key, restrict the key to YouTube Data API v3),
     the quota cost (1 unit per `videos.list`/`channels.list` call; about 4,300 units
     per day if both polls run for 24 hours, against the 10,000 default; polling only
     runs while a widget uses the metric), and that a fixed
     video ID must be replaced for every new broadcast (follow mode avoids that).
   - Dialog: the key hint links to that wiki section; the fixed-video field states
     "Each new live broadcast has a new video ID: enter it, verify and activate before
     going live."
   - Refresh `src/docs/wiki/images/cc-audience-youtube.png`.

## Non-goals

- No `search.list` (100 units per call) or any other quota-heavy lookup.
- No following of incoming commentator feeds: only the Sheet Channel tab's YouTube
  channel. The existing sentence "Incoming commentator feeds do not change this
  selection" stays true in both modes.
- No change to Twitch audience.
- No automatic re-capture of net-goal baselines.

## Anti-goals

- Widgets must never show a previous broadcast's likes/viewers as if they belonged to
  the current one.
- Follow mode must not add API quota beyond today's polling (the live lookup stays on
  yt-dlp).
- The API key must still never leave the machine or travel with a profile.

## Details

### Mode and storage

- The public target file `<profile>/audience-youtube.json` gains a mode:
  `"video"` (today's fixed target, default for existing files) or `"follow"`.
  In `"follow"` it stores the pinned channel ID and its Channel-tab row text, never a
  video ID (the live video is resolved at run time).
- Verify in follow mode: requires a YouTube channel in the Channel tab (else fails
  with a clear message, e.g. "No YouTube channel in the Sheet Channel tab"). It checks
  the key and the channel via `channels.list` (subscribers) and, if a live video is
  found at that moment, `videos.list` once. "Not live right now" is not a verify
  failure.
- Activate stores the mode; the existing verify → activate flow and attempt checks
  stay.

### Live lookup

- Reuse the relay's existing resolution (`yt_live_video_ids` over the Channel tab's
  YouTube entry, 30 s cadence, cookies as today) rather than a second yt-dlp loop.
  *Builder's call:* how the audience service receives the current live IDs (e.g. the
  broadcast-chat supervisor publishes them; or a shared small resolver both use).
  It must also work when the broadcast-chat reader is disabled
  (`--no-broadcast-chat`) as long as a Sheet is configured.
- Newer of two: decide by `liveStreamingDetails.actualStartTime` from a single
  `videos.list` call with both IDs (still 1 unit), not by list order.
- On a change of the followed video, cached video metrics are invalidated
  immediately (same as a target change today), so no old value is shown under the
  new broadcast.

### Which channel, and pinning it (my calls)

- **One channel per connection.** If the Channel tab has one YouTube row, follow mode
  uses it. If it has several, the dialog lists the YouTube rows and the producer picks
  one (default: the first). The "newer of two live videos" rule applies within that
  one channel.
- **Pinned at verify.** Verify resolves the chosen row to a concrete channel ID and
  Activate stores it (plus the row text it came from). The connection then follows that
  channel ID. If the Channel tab later no longer contains that channel, the connection
  shows "Channel tab changed; verify again" and video metrics are unavailable until the
  producer verifies again. Subscriber values keep following the pinned channel ID.
- **Resolving the channel ID.** `@handle`, `UC…` IDs and `/user/` URLs map directly
  (`channel_target()`); any other form the tab accepts (e.g. `/c/customname`) is
  resolved once at verify via yt-dlp channel metadata (`channel_id`), so it works when
  nothing is live. If neither works, Verify fails with "Use an @handle or channel ID in
  the Sheet Channel tab".
- **No Channel tab** (e.g. a custom `--sheet-csv-url` without one): follow mode is
  unavailable and the dialog says why; fixed video mode still works.

### Lookup failures vs. not live (my call)

- The resolver must distinguish "lookup failed" from "nothing live" (today
  `yt_live_video_ids` returns `[]` for both; extend it or add a sibling without
  changing the chat supervisor's behaviour).
- On a failed lookup the connection keeps the last followed video. It drops it only
  when `videos.list` reports it is no longer live (`liveBroadcastContent` not `live` /
  `actualEndTime` set) or a newer live video replaces it. This keeps the anti-goal: the
  API confirms the kept video is still the live one.
- "Not live right now" is shown only after a successful lookup with no live video.

### Net goals (likes)

- *My call:* existing behaviour stays: a likes net-goal baseline is tied to the
  video identity; when follow mode switches to a new video, the widget shows the
  existing "Source changed; capture a new baseline" until the producer recaptures.

### Dialog

- A mode choice: **Follow the live broadcast of the Sheet channel** / **Fixed video**.
  Follow shows the resolved channel and the current state (live video title, or
  "Not live right now"); fixed video shows today's video and channel fields.
- Status text stays mechanism-only (what it does), no crew procedure.

## Constraints

- Repo rules (`CLAUDE.md`): edit only under `src/`, English, stdlib, TDD, outbound
  HTTP via `http_util` where covered, `errors="replace"` on subprocess text.
- Changed Control Center view → refresh `cc-audience-youtube.png` in the same change;
  run the ui-visual-verification skill.

## Left to the builder

- How live IDs flow from the resolver to the audience service.
- Exact JSON field names for the mode.
- Dialog layout details.

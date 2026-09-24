# CLAUDE.md: `src/relay/` (the relay, `racecast-feeds.py`)

Loaded when working under `src/relay/`. The root CLAUDE.md keeps a short summary
and the security boundaries; this file holds the relay's mechanisms, gotchas and
design rationale. Read it before changing the relay or any page or endpoint it
serves, even when the file you edit lives elsewhere (`src/obs/`, `src/director/`,
`src/cockpit/`, `src/console/`, `src/racecontrol/`, `src/scripts/`, `tests/`).

## The relay, the heart
A 2-feed "ping-pong": **Feed A** (port 53001) serves odd stints, **Feed B** (53002)
even stints; at each handover the off-air feed advances to the next stint's
commentator stream, so OBS media sources never change URL. A 3rd **POV** feed
(53003) is an optional driver picture-in-picture, paused at start. The schedule is a
Google-Sheet tab read as CSV (no API key); a running feed is never torn off
mid-stint: sheet edits apply on the next `/next` (handover) or `/reload`.
**Qualifying mode** (issue #124): a second `ScheduleSource` reads a separate
`Qualifying` tab (same URL/Streamer/Stint structure); `Relay.mode` ∈
{race, qualifying} and `self.source` is a property returning the active one, so
every path (status/next/reload/set_stint/handover) is mode-aware. Qualifying is a
single stream → it lands on Feed A (B idles). Switch at launch (`--qualifying` /
`racecast event start --qualifying`) or live via `/mode/race`|`/mode/qualifying`
(`set_mode`, re-points feeds like a takeover); the panel has a Qualifying section
(mode toggle + a one-row editor writing the Qualifying tab via the `schedule`
webhook action with `tab:"Qualifying"`). On switch the HUD Streamer/Stint follow
the qualifying row (the issue #112 path).
**Qualifying in the event lifecycle.** Qualifying is also a first-class *broadcast
Part*: a `Q` row in the `Producer` tab (its own Stream Key) is the qualifying
broadcast, and the Director-Panel Parts control is **mode-gated**,
`producer.active_producer_rows(rows, mode)` selects the numeric parts in race mode and
the single `Q` part in qualifying mode (a part is qualifying iff its label, uppercased,
starts with `Q`). Because `Q` is the only (= last) part, ending it fires the existing
last-part auto-stop → report → teardown, so `racecast event start --qualifying` (or the
Control Center **Qualifying** toggle at Start Event, `ui_ops` `_qualifying_flag`) runs a
clean, separate qualifying session with its own OBS stream key. `set_mode` resets the
part pointer to Part 1 on a live mode switch (unless a part is on air). The post-event
**report title** gains a `— Qualifying` marker (`_relay_mode`/`_qualifying_title` in
`racecast.py`, read from the live relay `/status`). The **Director Panel** shows the
schedule editor matching the mode (race editor `#urlsBox` vs the qualifying row
`#qualRow`; the mode toggle, submissions and Parts control stay visible in both). The
cockpit tally/plan, the race-control desk, `/schedule/data` and submission *target*
resolution are already mode-aware (they read the mode-aware `relay.source`); cockpit
stream-link **submissions record their `mode`** so the director's approve writes the
qualifying row via `qualifying_set` (the confirm phrase for the Q part reads
`START PART Q`). Spec/plan:
`docs/superpowers/{specs,plans}/2026-07-05-qualifying-event-lifecycle*.md`.

Pull pipeline per feed: **YouTube**: `yt-dlp -g` resolves the live HLS URL (passing
YouTube's bot-check via `yt-cookies.txt` + deno JS challenge) → `streamlink
--player-external-http` serves that URL to one OBS client. **Twitch**: routed directly
through Streamlink's Twitch plugin (no yt-dlp hop); gated feeds optionally use
`twitch-cookies.txt`. **Local** (#592), a Schedule URL cell `local:` reads the
producer machine's capture card (`RACECAST_CAPTURE`; game audio defaults to the card's own audio device found by name in ffmpeg's device list: `scan_capture_audio`, override/`none` via `RACECAST_CAPTURE_AUDIO`, from the
machine `.env`) with the relay's own `ffmpeg` writing MPEG-TS to stdout at the same fan-out
seam (`local_capture_cmd`, bitrate capped by `LOCAL_VIDEO_KBPS` against the 16 MB ring);
no resolve, cookies or quality tiers, fan-out required. The producer's commentary mic
for that stint is the OBS input `Commentary Mic Device` (#593, in `Stint` + `Splitscreen`,
shipped muted, `RACECAST_MIC`): the relay opens it only while the local feed is on air
and mutes it on every other handover/SPLIT (`obs_ws.feed_audio_plan`, snapshotted in
`Relay.obs_audio_plan`; only on an endurance machine with `RACECAST_CAPTURE`, never in
solo, where the mic ships hot). A hand-picked STINT A/B uses the same plan through
`GET /obs/stint/<A|B>` / `POST /obs/stint` (`apply_stint_state`), the panel macro calls
only that, Companion calls it after its direct OBS actions (the break-glass path when the
relay cannot reach OBS; they never touch the mic), and the smoke test does the same. A failed switch (collection imported before #593) is a relay-log WARNING. Only the director/sheet can set
it (`is_feed_source`); the commentator submit and POV paths stay on `is_channel`. (`curl`-ing a feed port returns nothing, it serves a single
consumer; that is not a failure.)

**Feed fan-out (`RACECAST_FEED_FANOUT`, machine `.env`, DEFAULT ON, live-verified 2026-06-29; set `=0` to fall back).** By default the relay is the single `streamlink --stdout` consumer per feed and re-serves the byte stream via an in-relay `FeedFanoutServer` (`FeedRing` bounded byte ring on the same loopback feed port) to OBS *and* the Director Panel preview simultaneously, eliminating the ~2 s stale-on-activation glitch and making the preview tap free (no second pull). Health moves from "serve process exited" to "bytes stopped flowing": a byte-stall watchdog (`RACECAST_FEED_STALL_S`, default 20 s since #488; `FANOUT_STALL_S = 8.0` survives only as `feed_stalled`'s default parameter) kills the reader and drives the DROP state. Because that makes the watchdog the authority on a dead source, streamlink's own early stop is **derived** from it here (`queue_deadline_factor`, fed to `--stream-segmented-queue-deadline` on both platforms) so it can never fire first: the flag is a multiplier on the playlist's `#EXT-X-TARGETDURATION` (measured 2026-09-21: YouTube 5, Twitch 6), which the broadcaster picks, so a fixed factor silently inverts the order whenever the watchdog moves, which is what #488 did when it raised the watchdog 8 → 20 s. Direct-serve has no watchdog, there streamlink's early stop *is* the detector and keeps `QUEUE_DEADLINE_FACTOR`. The fan-out reader PIPEs streamlink's stderr (its only diagnostic channel here, stdout is the video bytes) and pumps it to `feed_X.log` with a `[streamlink]` tag, parity with direct-serve; discarding it once left every fan-out stall/EOF unexplained. **Caveat. VOD vs live:** because the in-relay ring overflows (the reader never blocks), there is NO consumer backpressure, so against a finite **VOD** streamlink races ahead at multiples of real-time → it reaches EOF in minutes (reconnect churn) and its bursty re-fetch can trip the stall watchdog; a real **live** stream is naturally throttled to ~1× at the live edge, so neither happens. A just-dropped *served* feed is a SILENT health blip until it stays down past `HEALTH_CONNECTING_SETTLE_S` (15 s, below the 30 s red grace), so a reconnect that self-heals within a heartbeat does not fire a DEGRADED Discord `@here` (`drop_connecting_notifiable`, unit-tested); a longer stall still surfaces yellow before red, and red (genuine loss) is immediate. The relay also **measures how far OBS is behind the live edge** (#583): `_serve` records the position OBS has accepted at the start of every read cycle (the read itself jumps the cursor to the trailing mark, so only that position shows a slow consumer), `FeedRing.age_at_offset` turns it into seconds, `consumer_backlog` is the live value on `/status` (Director-Panel header pill `BEHIND LIVE`) and `take_backlog_floor` is the per-heartbeat minimum stored in `health-history.db` v9. A floor more than `RACECAST_FEED_BACKLOG_WARN_S` (5 s, uncalibrated until #584) beyond the prebuffer is a quiet, never-paging yellow and a cockpit note (`program_behind_s`), and since 2026-09-21 the relay **sheds it by itself**: `_backlog_shed_tick` (heartbeat, right after the classification it reads) rebuilds the ON-AIR feed's OBS input so the picture returns to the live edge without a director pressing RESET. It is a second REASON on the freeze detector's control, not a second automation, same `f._obs_reconnect()`, same `RebuildGuard` (three ineffective rebuilds then stand down), same cooldown, because two automations turning one control is a race someone debugs mid-broadcast. `RebuildGuard.pending` is therefore a reason tag, and each reason judges only its own rebuild with its own signal; a rebuild still unjudged when the next one fires counts as ineffective rather than being overwritten. The rebuild registers a splice (`_note_obs_splice`, which takes no clock on purpose) so the A/V detector does not flag the relay's own remedy as unexplained. Off by `RACECAST_FEED_BACKLOG_SHED=0`; `RACECAST_FEED_BACKLOG_SHED_TICKS` (default 1) raises the streak, one tick being enough because the classified floor is already a whole-interval minimum. On-air feed only (an off-air one has no consumer under `close_when_inactive`), POV excluded. This **overrides** #581's *"only a human chooses it"*, which came out of Catalunya's 26 black dropouts; the producer's standing preference is automatic recovery with the manual `/obs/feed-reset` (#587) as the fallback, and `RebuildGuard` plus the measured backlog are what make the override survivable. A backlog that keeps GROWING is NOT fixed here (the shed shears it, the slow host rebuilds it, the guard stands down with a warning), that remains #585, whose trigger is stated in seconds per minute: `racecast obs benchmark` reports exactly that as each tier's `backlog_growth_s_per_min`, with `_source_ahead`'s share subtracted so a source still catching up to the live edge does not read as a slow host. See `docs/superpowers/specs/2026-09-21-automatic-backlog-shed-design.md`. `/status` also carries each feed's `inbound_max_gap_s`, the heartbeat's last #535 reading. It is published read-only, so a 2 s poll never steals `take_max_inbound_gap`'s reset. Its gate is serving + fan-out, which is NOT `backlog_s`'s gate: `backlog_s` also needs an attached consumer, while the gap is measured on the source side and stays valid while OBS is away for a rebuild (blanking it there fed the benchmark a leading run of `None`, which let the pre-restart reading through). The heartbeat keeps two maps for this: `_interval_max_gaps` raw for health-history.db, and `_served_max_gaps` with `None` for an interval nothing measured, so a feed going on air between two ticks cannot republish the idle `0.0` as a healthy-looking measurement. `racecast obs benchmark` records it next to the backlog (#619), since the backlog alone shows a bursty source and a slow consumer the same way. Its resolution is the heartbeat's 30 s, and it drops the reading a window inherits from before the restart, anchored on the first actual reading so a leading `None` run cannot smuggle it back in. That rule (`feed_backlog_degraded` + both env parsers) lives in `health_store.py` so the post-event report's backlog-led finding (#586, `report_build._finding`) counts exactly what the director saw; the report reads the configured OBS frame rate from health-store v10 `obs_fps_target`. The relay unconditionally sets `Feed A`/`Feed B` `close_when_inactive` to match the flag. `True` when fan-out is on (OBS drops off-air so no stale backlog forms), `False` when off (restores the safe direct-serve state so a fallback never leaves feeds stuck at `True`); `Feed POV` ships `True` in both modes and is left untouched (best-effort, OBS unreachable → a note, never a crash). Setting `RACECAST_FEED_FANOUT=0` falls back to the proven `--player-external-http` direct-serve path (one streamlink process → one OBS consumer), the coexistence switch stays so a producer can revert instantly; a transport choice, not a league setting. By default OBS and the program-audio monitor are **served only up to a trailing high-water mark `RACECAST_FEED_PREBUFFER_S` seconds behind the live edge** (default 3 s, #533), every read is capped there, not just the join, so a greedy consumer cannot outrun it, holding an in-ring reserve that absorbs bursty-source gaps (`=0` restores the live-edge serve); the Director-Panel preview still taps the live edge. **A restart rejoins OBS** (#614): since the relay owns the socket, a restart would otherwise splice the new stream into OBS's stale demuxer, so `should_obs_reconnect` rebuilds the feed input after a drop **and** whenever a consumer is still attached to the ring, the director's `/reload`, a tier change, and `set_index` on an on-air feed (every `/next` in solo/qualifying). The ping-pong handover is excluded: `close_when_inactive` already dropped the off-air feed. The rejoin **waits out the HLS prefetch burst** first, because the trailing mark is keyed on byte ARRIVAL: a burst that lands inside the prebuffer window sits entirely above the mark, so an immediate rejoin would put OBS at the burst's START, 10-19 s behind live. How long the burst takes to ARRIVE is a download duration (downlink, source bitrate, CDN), so the relay **measures it per serve** instead of predicting it: the rejoin thread watches `last_byte_ts` and takes the first inbound idle of `BURST_IDLE_S` as the burst's end, then waits the prebuffer, then rebuilds. Only two constants are fixed, and both describe the SOURCE's segment cadence, not the connection: `BURST_IDLE_S` (1.0 s: measured 2026-09-20 on YouTube and Twitch, the usable window is ~(0.78, 1.4) because gaps *inside* a burst reach 0.78 s on YouTube while Twitch low-latency's steady cadence is only 1.4-1.9 s) and `SEGMENT_FETCH_BUDGET_S` (1.0 s) as a **ceiling** via `prefetch_land_s(segments, prebuffer_s)`, for a source that never pauses that long. Twitch low-latency does not. A flat 5 s constant was ~3 s short for YouTube ROBUST, which is what started this. Re-measure with `tools/prefetch-burst-probe.py` (no relay, no league needed). A local capture feed (#592) has no `--hls-live-edge` and never waits; a rejoin whose serve was superseded or died during the wait no-ops (`rejoin_is_stale`). `/status` exposes `feed_prebuffer_s` so `racecast obs benchmark` derives the same wait. See `docs/superpowers/specs/2026-06-28-relay-feed-fanout-design.md`.

Control is an **unauthenticated** `ThreadingHTTPServer` on port `8088` exposing GET
endpoints (`/next`, `/reload`, `/set/A/<n>`, `/pov/reload`, `/timer/*`, `/status`,
`/panel`, plus the served pages `/hud`, `/splitscreen` and the per-league overlay assets
`/hud/override.css`, `/splitscreen/override.css`, `/overlay/fonts/<file>`, …)
driven by Companion's Generic-HTTP module. `--bind` defaults to **`auto`** (plug &
play): it binds `127.0.0.1` (OBS always reaches the HUD/feeds on the fixed loopback
address: the OBS collection never needs editing) **and** this machine's Tailscale IP
(auto-detected via `detect_tailscale_ip()`, the `100.64.0.0/10` CGNAT range) when
present, so remote directors/tablets reach `/panel` + `/hud` over the tailnet: *without*
exposing the unauthenticated server on the local LAN the way `0.0.0.0` would. If
Tailscale is down, `auto` falls back to localhost-only (OBS keeps working). Pass an
explicit value (`127.0.0.1` for local-only, or `0.0.0.0`) to override. The endpoints
have no auth and `/status` reveals stream URLs, so the tailnet is the trust boundary,
keep it to invited members. Bind logic is pure + unit-tested: `tests/test_bind.py`.

**Logging.** The relay and each static-stream feed write timestamped, leveled lines
(`YYYY-MM-DD HH:MM:SS LEVEL …`) to per-service log files under `runtime/<profile>/logs/`
via `src/scripts/logsetup.py` (`TimedRotatingFileHandler`, daily midnight rotation,
archive suffix `.YYYY-MM-DD`). Old archives are pruned on each service start: the
retention window defaults to 7 days and is overridable with
`RACECAST_LOG_RETENTION_DAYS`. Each relay feed has its own `feed_A/B/POV.log`; the
streamlink child's output is pumped through the feed logger with a `[streamlink]` tag
and classified levels (ERROR for 4xx/fatal, WARNING for retries). The `relay` and
`streams` CLI log sources are **merged-file views** (console + all feed logs in one
stream); `aggregate` is the default Control Center source and merges all live sources
(relay, streams, OBS, Companion, Tailscale). OBS Studio and Companion logs are
read-only from their native app directories; the Tailscale source appends a
timestamped `tailscale status` snapshot on each service start and on
`racecast tailscale status`. Archive history is accessible with
`relay|streams logs --list` / `--archive <date>` (racecast sources) or by filename
token (OBS/Companion).

The same server also hosts the **lower-third HUD** as one relay-served page,
replacing ~13 cropped Google-Sheets-editor browser sources (the old producer-lag
culprit): `/hud` serves `src/obs/hud.html`, `/hud/data` returns the overlay JSON
(`HudSource` reads the **Overlay** tab for live values + the **Configuration** tab's
brand-text column, header `Brand Name`/`Brand Key`/`Brand`, see `BRAND_TEXT_HEADERS`,
for team→manufacturer), and `/hud/assets/{flags,brands}/<name>`
serves bundled logos from `src/assets/`. The page polls `/hud/data` (no manual
reloads); flags/brands resolve from text via `asset_key()`. Flags: `--no-hud`,
`--overlay-tab`, `--config-tab`, `--hud-poll`, `--overlay-dir` (per-league override
CSS/fonts, passed by the CLI when `profiles/<active>/overlay` exists). The optional
**Quali Times** tab (`--quali-times-tab`, default `Quali Times`) adds each car's
qualifying best lap. Its fetch is **off the HUD refresh path entirely**
(`HudSource.refresh_quali`, called once at boot + by its own `quali_poller` thread every
`QUALI_TIMES_POLL_S` = 60 s, the laps are entered once between qualifying and the race),
so no quali-tab state can delay an on-air `refresh()` or a synchronous panel-push confirm;
`refresh()` only reads the last-good map. A fetch/parse failure keeps that **last-good**
map (never rolled back to empty) and warns once, an
existing tab whose header was renamed/removed replaces the map with empty, and a league
that never created the tab simply stays empty. A lap is matched per **car**: the verbatim
`Team` cell first, then the `#NNN`-stripped name, so two cars of one team keep their own
lap while a bare row still matches every car. The Configuration tab's `BG Color`/`Text
Color` columns surface as `teams[].bgColor`/`textColor`, and `/hud/data` also carries the
relay's `mode`. Tests: `tests/test_hud.py`.

The panel's **sheet controls** write back through one Apps Script webhook
(`RACECAST_SHEET_PUSH_URL`, injected by the CLI from the active profile's
`SHEET_PUSH_URL`, shared with the race timer, wiki: Sheet-Webhook):
Setup fields (Stint label/Streamer/Session/Race Control) are async-optimistic
(`HudSource` override now, sheet poll confirms, 30 s expiry), Schedule/POV URL
writes are synchronous; URL changes never auto-reload a feed. Setup "Stint" =
HUD display label, NOT the feed stint index. `SetupControl` + endpoints
`/setup/*`, `/schedule/*`, `/pov/set` (POST). Tests: `tests/test_setup.py`.

The relay also hosts a **crew chat** (`GET /chat/data`, `POST /chat/send`,
`GET /chat/reload`): an in-memory ring buffer (400 messages) persisted to
`runtime/<profile>/chat.json`. The panel polls `/chat/data`; messages render via
`textContent` (XSS-safe); the unread badge is keyed on server `ts` (handover-safe).
There is **no destructive HTTP endpoint**: clear/import/pull are producer-only CLI
actions (`racecast chat clear|pull|import|export`, logic in
`src/scripts/chat_admin.py`) that write the file and trigger `/chat/reload`. The
tailnet is the trust boundary (unauthenticated, like the rest of the relay).
Tests: `tests/test_chat.py`.

The relay also hosts a **read-only broadcast-chat reader** (issue #294): a mirror of
the event's **public YouTube and Twitch** broadcast chat inside the `/console` pages (cockpit,
director panel, race-control desk) so the crew can follow it without a separate browser
tab. The broadcast channel(s) come from a Sheet **`Channel`** tab (header `Platform |
Channel`; `Channel` holds a channel URL / `@handle` / `UC…` id, **never** a video id),
read by `ChannelSource` (mirrors `CrewSource`); derived from `SHEET_ID` like the crew
roster, so a custom `--sheet-csv-url` or `--no-broadcast-chat` disables it (flag
`--channel-tab`, default `Channel`). `BroadcastChatSupervisor` reconciles, each ~30 s
cycle, a DESIRED set of readers keyed by a stable id (stop those no longer desired,
start new ones, retry a died one unless it is tombstoned):
- **YouTube**, one `_BroadcastReader` per **currently-live videoId**, resolved via
  yt-dlp (the **`/streams`** tab so CONCURRENT live streams are all found, the
  producer-handover overlap where B's stream starts before A's ends: with `/live` as
  the single-stream fallback; **public streams only**). Each reader bootstraps from the
  `live_chat` page then follows the **Innertube `get_live_chat` continuation**, a native
  stdlib poller (relay-owned network, like `CrewSource`, **exempt** from the `http_util`
  UA guard; it must send a browser `User-Agent` or Innertube 403s). A genuinely-ended
  stream is *tombstoned* (not restarted until its videoId leaves the live set).
- **Twitch** (Phase 2), one `_TwitchReader` per **channel login** (`twitch:<login>`): a
  persistent **anonymous IRC** connection (`irc.chat.twitch.tv:6697` over TLS, a
  `justinfan` nick, `CAP REQ twitch.tv/tags`, `JOIN #login`) that needs **no API key or
  OAuth**: pure stdlib `socket`+`ssl`. It reconnects on drop with backoff; the login is
  strictly validated (`twitch_login`, `[a-z0-9_]{1,25}`) so a channel value can never
  inject IRC commands. `PRIVMSG`s are parsed by `parse_twitch_privmsg` (display-name +
  message id + `tmi-sent-ts` from the tags).
`BroadcastChatStore`
is an **ephemeral** in-memory ring (`broadcast_chat.MAX_MESSAGES = 500`), dedup-by-id,
**ts-merged across streams** (so a handover overlap renders as one continuous chat,
tagged by source, videoId or `twitch:<login>`), **never persisted, no write path**. Endpoints:
`GET /broadcast-chat/data` (tailnet/loopback) + `GET /console/broadcast-chat/data`
(Funnel, **ANY-auth under the existing `/console` mount → no new public surface**), both
read-only. The data is already public on the platform, so mirroring it leaks nothing; if the
reader is disabled the endpoints 404 and the front-end card self-hides. A read-only
`target` (`{platform, url}`) field on `/broadcast-chat/data` (and the Funnel
`/console/broadcast-chat/data`) carries the current primary live source so each console
card can show a **"Write in chat ↗"** button that `window.open`s the native YouTube/Twitch
popout chat: the crew posts under their **own browser account**; the relay adds **no write
path** and stays read-only/ephemeral. The target is computed each supervisor cycle (pure
`primary_chat_target` in `broadcast_chat.py`, from the already-resolved live set, KISS:
first live source) and exposed via `BroadcastChatStore.set_target`; the front-end gates the
button on a non-null `target` and validates the URL client-side (`bchatUrlOk`: https +
platform host, mirroring `emote_url_ok`) before opening. **Backend
choice (YouTube):** a native Innertube poller (no new dependency) over `chat-downloader`,
because the product ships as a single binary and broadcast chat is a non-critical
convenience panel that degrades gracefully (fragile vs. YouTube changes → empty, never
crashes the relay); the fetch sits behind a seam so `chat-downloader` could be slotted
in later. Pure parsers (Innertube bootstrap / `get_live_chat` / `runs→text`, the
`Channel` CSV, `live_set_diff`, the URL builders, and the Twitch `twitch_login` /
`parse_twitch_privmsg`) live in `src/scripts/broadcast_chat.py`; the network + threads
are in the relay. Front-end: a read-only "Broadcast chat" card in the three pages, polled
via the `RC_API` shim (tailnet + Funnel), rendered with `textContent` + a per-message
timestamp + a source badge on a handover overlap (no front-end change was needed for
Twitch, it flows through the same store/endpoint/card). Tests:
`tests/test_broadcast_chat.py` (pure parsers + store + endpoint). Live diagnostic
(maintainer, not shipped): `tools/broadcast-chat-probe.py <channel>` resolves + tails a
live channel's chat standalone (YouTube via yt-dlp+Innertube, or `--twitch` / a
`twitch.tv` URL via anonymous IRC): the way to validate the real path against a live
stream.

The relay also provides a **director→commentator text-cue channel** (an IFB-lite, text-only
stand-in for an earpiece): the Director Panel's **Cues** section lets a director pick a
target (a specific commentator, **All commentators**, or **On air**: resolved server-side at
send time), choose a level, and send a short cue. **`Info`** cues auto-expire after 30 s
and appear as a brief toast in the commentator's cockpit; **`Critical`** cues are sticky banners
the commentator must **Acknowledge**: after which the director sees a **✓ seen** stamp.
Quick-cue **presets** come from a `Cue Preset` column in the Sheet's **Configuration** tab
(same admin-managed vocabulary model as Race Control); free text is always available and
is the only option when the Configuration tab is unreachable. Endpoints `POST /cues/send`,
`GET /cues/data`, `GET /cues/presets`, `GET /cues/reload` are **director**-gated; commentator
endpoints `GET /cockpit/cues` + `POST /cockpit/cues/ack` are identity-scoped to the
token's own commentator. All are reachable via Funnel only through the existing `/console`
mount: no new public surface. Persisted to `runtime/<profile>/cues.json`; producer
takeover (tailnet + `--funnel`) pulls A's still-active cues via `/console/takeover/cues`.
Pure logic: `src/scripts/cue_admin.py`. Tests: `tests/test_cues.py`.

The relay also serves an optional **on-air program-audio monitor**: the on-air
feed's audio, encoded to an endless MP3 stream and offered as a toggle next to the
silent program still on the Director Panel, Commentator Cockpit, and Race Control
desk. Endpoints `GET /preview/program-audio` (director; ANY) and
`GET /console/cockpit/program-audio` (cockpit + race-control; ANY, funnelled under
the existing `/console` mount: no new public surface). One on-demand ffmpeg
(`libmp3lame`, codec parameterized via `PROGRAM_AUDIO_*` constants) taps the feed
fan-out ring and is re-served to many listeners from one output `FeedRing`
(`ProgramAudioService`, reference-counted + idle-reaped: zero cost when nobody
listens); it follows the on-air feed across handovers by restarting on the new
feed's ring (MP3 frames splice, brief silence gap). Requires fan-out (endpoints
404 otherwise; the front-end card self-hides). Default ON; kill-switch
`RACECAST_PROGRAM_AUDIO=0`. NOT the full OBS program mix: feed-audio only (see
`docs/superpowers/specs/2026-07-02-program-audio-monitor-design.md`). Tests:
`tests/test_program_audio.py`.

The relay also serves a **commentator-facing Commentator Cockpit** (issue #191) under an
auth-gated `/cockpit/*` namespace: a live program monitor (reusing
`get_program_screenshot`), an "ON AIR / UP NEXT" tally (`cockpit_tally`, derived from the
on-air feed + the live schedule via `asset_key`-normalised streamer names), the embedded
crew chat (identity forced to the token's streamer), and a read-only timer. A read-only **stint plan** (right column, below the timer) lists the full running order (stint label + streamer name) from a redacted `schedule` field on `/cockpit/data`, no stream URLs (the same Funnel redaction boundary as `/console/takeover/status`); the on-air stint and the viewer's own stints are highlighted (pure `cockpit_schedule`). It is exposed
**publicly via Tailscale Funnel**, which maps **only** the `/console` path prefix to
`127.0.0.1:8088`: the rest of the relay stays tailnet/loopback-only and is **never**
funnelled (the security boundary). `/console/buttons` reverse-proxies (HTTP + a raw-WebSocket
passthrough for Companion's tRPC `/trpc`) to the resolved local Companion bind address,
director-gated (#236); it is a sub-path of the single `/console` mount (no second mount);
OBS-WebSocket remains never funnelled. Funnel passes no Tailscale identity, so auth is 100%
server-side: a per-person token `<streamer_key>.<version>.<sig>` signed with the
**per-league** `CONSOLE_SECRET` (`profiles/<name>/profile.env`, travels with `profile
export`); revocation bumps a streamer's version in
`runtime/<profile>/console-versions.json`.
The cockpit is **zero-config**: the secret is **auto-provisioned** by the CLI on first relay
start (`_ensure_active_cockpit_secret` in `src/racecast.py`, idempotent, never the shipped
`example` profile), so `/cockpit/*` is live **whenever a secret exists**: there is no
separate enable flag. When the secret is absent every `/cockpit/*` path 404s (like chat/timer
when disabled). PUBLIC exposure is the **independent Funnel switch** (`racecast funnel on`),
which mounts **only** `/console`, the only way `/console` leaves the tailnet. The token
rides in the `…/console?t=` link once, then an `HttpOnly; Secure; SameSite=Lax`
`rc_console` cookie. **Discord OAuth second front door:** when `DISCORD_CLIENT_ID` +
`DISCORD_CLIENT_SECRET` are set in `profile.env`, the relay also serves
`/console/login` + `/console/oauth/callback` (scope `identify`); a session-bound
`rc_oauth_state` cookie guards CSRF; on a Crew-tab Discord-handle match the relay mints
the same `rc_console` token. The Crew tab gained `Commentator` and `Discord` columns;
`resolve_roles` is an A1 union (Schedule OR Crew Commentator flag). Auth core:
`src/scripts/console_auth.py`; revocation store: `src/scripts/console_admin.py`;
commentator page: `src/cockpit/cockpit.html`; CLI: `racecast console …`; takeover pulls A's
versions over the tailnet (like `chat pull`). Tests: `tests/test_cockpit.py`. The crew
roster (Crew tab ∪ live Schedule) is exposed via a tailnet-only `GET /crew/data` endpoint
(root path, **never** funnelled, only `/console` is mounted); `racecast links` unions Crew
∪ Schedule to produce role-adaptive `/console` links for every person. The Control Center
cockpit view is now called **"Crew Console"**.

**Producer takeover over Funnel (`/console/takeover/*`, issue #216 Phase 7).** When
producer B is not on the tailnet, `racecast event takeover <A-magicdns-host> --funnel`
pulls the handover state over A's public Funnel. Three read-only endpoints live under
`/console/takeover/` (all reachable via Funnel, **never** adding to the public surface
beyond the existing `/console` mount):
- `GET /console/takeover/status`, **redacted** status: only `live`, `league`,
  `event_title`, `timer`, and `mode`. Feed stream URLs are stripped; they never leave
  the tailnet. This is an allowlist, not a blocklist.
- `GET /console/takeover/chat`: the full chat history (same payload as `/chat/data`).
- `GET /console/takeover/versions`: the console-versions revocation map (same payload
  as `/cockpit/versions`).

All three require the **step-up** `X-Console-Secret` header (legacy name `X-Cockpit-Secret`
still accepted for one release) carrying the shared per-league `CONSOLE_SECRET` (producer-level
auth: the same secret that signs commentator tokens). A
wrong secret returns HTTP 403; the client aborts loudly. A network failure falls back to the
local `--stint N` bringup. On success, B's relay is brought up via the normal `event start`
path with the adopted stint/league/title/mode, and chat + versions are applied locally
(`ca.apply_pulled` / `cpadm.apply_pulled`, same as the tailnet pull path). The tailnet path
(`racecast event takeover <100.x-ip>`) is unchanged and does not use the step-up header.
The security boundary is preserved: only `/console` is Funnel-mounted (with `/console/buttons`
as a director-gated relay-proxy sub-path: no second mount); no takeover endpoint is reachable
without the step-up secret; feed URLs stay tailnet-only; OBS-WebSocket is never funnelled. CLI helper:
`_funnel_takeover_base(host)` + `_takeover_get(url, secret, timeout)`; plan:
`docs/superpowers/plans/2026-06-19-console-roles-phase7-takeover-funnel.md`.

**Commentator stream-link submission (issue #193).** A write-scoped add-on: a commentator
submits a YouTube/Twitch URL for one of *their own* stints from the cockpit
(`POST /cockpit/submit`, the only write reachable over Funnel): token-auth + per-identity
rate limit + `is_channel()` SSRF guard + server-side **own-rows-only** check
(`own_submission_target`, `asset_key(streamer) == token's streamer_key`). It is stored
**pending** (never auto-published) in `runtime/<profile>/cockpit-pending.json` and pings
Discord (`cockpit_submission_payload`, no-op without a webhook). The director's
**list/approve/reject** live under a separate `/submissions/*` namespace that is **NOT**
funnelled (tailnet-only, reached from `/panel`); approve calls the existing
`SetupControl.schedule_set` (writes the Sheet; applies on the next `/reload`). Pure store +
audit log: `src/scripts/cockpit_submissions.py` (mirrors the `chat_admin.py` /
`console_admin.py` pure-store pattern); thin
thread-safe wrapper `SubmissionStore` + endpoints in the relay; panel section + cockpit
form in the two HTML files. Tests: `tests/test_submissions.py`.

**Role-adaptive /console pages (issue #216).** The relay also serves a `/console` launcher plus `/console/cockpit` and `/console/panel` pages, all role-gated behind the Phase 3a `/console` auth gate; page API calls resolve under the mount via an injected `window.RC_API_BASE` shim. Launcher, cockpit, and panel are in `src/console/console.html`, served with the authenticated subject's role-conditional links; `/console/whoami` returns the authenticated subject. Authorization is per-role (any authenticated subject reaches `/console` + `/console/cockpit`; directors reach `/console/panel`). Tests: `tests/test_console.py` + `tests/test_console_gate.py`.

**Race Control monitoring desk (issue #244).** A **fourth crew role**, `race_control`, a *read-only* monitoring desk: live **program preview** (reusing the cockpit's `get_program_screenshot`), the **redacted streamer/stint schedule** (no stream URLs, the `/console/takeover/status` redaction boundary), the **race timer**, and **crew chat** (identity forced from the token). It triggers **no broadcast actions**. NB: the role string is `race_control` (Crew tab); it shares its label with the director-only HUD `racecontrol` banner (Setup tab) but they never collide in code, that banner stays director-only and this role never writes to it. Roles are additive (a person can be e.g. both director and race_control). Capability `RACE_CONTROL` in `console_policy.py`; the Crew tab gained a **Race Control** column (`CREW_RACE_CONTROL_HEADERS`, header-located like Commentator); `CrewSource`'s canonical row is now a 6-tuple `(name, dir, prod, commentator, race_control, discord)` with a `race_control_keys()` helper; `resolve_roles` gains a `crew_race_control_keys` union. The relay serves `GET /console/race-control` → `src/racecontrol/race-control.html` and `GET /console/race-control/data` (the only new endpoint: `{schedule, event_title, mode, on_air}`, built by the pure `race_control_schedule()`), both gated `Requirement(RACE_CONTROL, False)`. The desk's program/timer/chat **reuse the existing `ANY` cockpit endpoints** (no new public surface). The launcher shows a **Race Control** card when whoami roles include `race_control`. Crew editor: a 6th "Race Control" checkbox round-trips through `/api/crew`. Tests: `tests/test_roles.py`, `tests/test_console.py`, `tests/test_console_gate.py`, `tests/test_cockpit.py`, `tests/test_ui_server.py`.

**Health Monitor.** The relay serves `/health-monitor` (tailnet/loopback) and `/console/health-monitor` (Funnel, any authenticated `/console` subject): a dashboard of relay health over time backed by a per-profile SQLite store at `runtime/<profile>/health-history.db`, sampled in the relay heartbeat; uPlot (MIT) is vendored at `src/assets/vendor/uplot/` (the first deliberately vendored JS in the repo, see the spec). CLI: `racecast health export|import|pull`.

**Relay-mediated OBS control (Director Panel).** The Director Panel's scene switches, visibility toggles, and audio controls go through the relay, not a direct browser→OBS-WebSocket connection. Six director-gated endpoints (all checked via `console_policy` before dispatch): `POST /obs/scene` (switch scene), `POST /obs/source` (show/hide a source), `POST /obs/audio` (set input volume/mute), `POST /obs/stream` (start/stop the OBS stream output), `POST /obs/state` (batch read of current scene + source visibility + audio levels), `POST /obs/refresh` (reload the relay-served OBS browser sources, the programmatic right-click → Refresh; best-effort; unconditional force). Beside those, `GET`/`POST /obs/split` sets the Splitscreen from the on-air feed (both feeds visible, on-air audio live, the rest muted; pure resolver `obs_ws.split_state_intents`) for the Companion `SPLIT` button and the panel macro, and `/obs/split-audio` is its audio-only predecessor, kept for older boards. `GET /obs/stint/<A|B>` / `POST /obs/stint` does the same for a hand-picked STINT A/B (visibility, audio, the commentary mic of a local stint) without touching the relay's on-air state; the scene cut stays with the caller for both. The relay calls `src/scripts/obs_ws.py` on the producer's machine, where the OBS-WebSocket password is auto-discovered from OBS's own config (overridable via `RACECAST_OBS_WS_PASSWORD` in `.env`); the password never crosses the network and OBS-WebSocket is **never** funnelled. The Director Panel therefore needs **no OBS IP, port, or password** from the director, the panel works fully over Funnel (`/console/panel`) using only the per-person token. The program monitor was already relay-mediated (`GET /preview/program`, any-auth, console-allowed) and is unchanged. All six OBS helpers follow the same best-effort contract as `get_program_screenshot`, they never raise. For the five control/read endpoints an unreachable OBS (`obs_ws._connect()` returning `None`) maps to a `503` with a descriptive note; `POST /obs/refresh` is best-effort fire-and-forget and instead returns `count: 0` with the note (a `503` only when obs-websocket support is absent entirely). Tests: `tests/test_obsws.py`.

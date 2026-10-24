# YouTube audience follow-live Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A YouTube audience connection can follow the live broadcast of the Sheet Channel tab's YouTube channel, and the setup is documented step by step.

**Architecture:** The relay gets a quota-free live-video tracker (yt-dlp, cached per channel, shared with the broadcast-chat supervisor) and a channel-ID resolver. `audience_youtube_connection.Service` gains a `follow` mode. That mode pins a channel ID at verify and asks the tracker every 30 s which video is live. It picks the newer of concurrent lives with one `videos.list` call and polls metrics for that video. The Control Center dialog gets a mode choice, and the wiki gets the API-key how-to.

**Tech Stack:** Python 3 stdlib (relay, scripts, runnable test scripts), vanilla JS in `src/ui/control-center.html`, Playwright e2e under `runtime/pw-venv`.

**Spec:** `docs/superpowers/specs/2026-10-23-audience-follow-live-design.md`

## Global Constraints

- Edit only under `src/` (plus `tests/`, `tools/`, `docs/`). English only. Stdlib only. TDD.
- Text-mode `subprocess` calls pass `errors="replace"`; yt-dlp calls use `external_tool_env()` and `_no_window_kwargs()` like `yt_live_video_ids`.
- No `search.list` and no other new YouTube API endpoint: only `videos` and `channels` through `audience_youtube.request`.
- The API key never leaves the machine and never travels with a profile (unchanged storage split).
- Widgets never show a previous broadcast's likes/viewers under the current one.
- Concurrent lives: the **newer** by `liveStreamingDetails.actualStartTime` wins.
- Status/copy is mechanism-only; no crew procedure.
- After Python changes: `python tools/lint.py`. Before the PR: `python tools/run-tests.py`, `python tools/build.py`.
- Changed Control Center view: run the `ui-visual-verification` skill and refresh `cc-audience-youtube.png` (wiki + slides copy) in the same change.

## Review Focus

- Relay restart in follow mode: saved follow targets + local key reconnect and follow without a new verify. Test: Task 3 `t_follow_mode_survives_restart`.
- An imported profile in follow mode without the local key shows today's REKEY message and does not poll. Test: Task 3 `t_follow_mode_without_key_needs_rekey`.
- The first lookup after start fails and there is no current video: state `lookup-failed`, video metrics unavailable, no crash. Test: Task 3 `t_follow_mode_lookup_failure_keeps_or_waits`.
- Concurrent lives where one is still `upcoming`: only actually-live videos compete. Test: Task 1 `t_newest_live_ignores_upcoming_and_ended`.
- Broadcast-chat disabled (`--no-broadcast-chat`) with a Sheet: follow mode still gets channels and live IDs. Test: Task 4 `t_follow_wiring_without_broadcast_chat`.

---

### Task 1: YouTube helpers for several video IDs

**Files:**
- Modify: `src/scripts/audience_youtube.py` (`request` allowed patterns; new `newest_live`)
- Test: `tests/test_audience_youtube.py`

**Interfaces:**
- Produces:
  - `VIDEO_IDS = re.compile(r"[A-Za-z0-9_-]{11}(?:,[A-Za-z0-9_-]{11}){0,4}\Z")`. `request("videos", {"id": "a,b"}, key)` accepts 1 to 5 comma-joined IDs; `channels` is unchanged.
  - `newest_live(payload, ids) -> tuple[str, str] | None`. Returns `(video_id, title)` of the item whose `id` is in `ids`, whose `snippet.liveBroadcastContent == "live"`, which has `liveStreamingDetails.actualStartTime` and has no `actualEndTime`, with the latest `actualStartTime` (ISO 8601, compare as parsed UTC datetimes). Returns `None` when none qualifies. Raises `ValueError` on a malformed payload (same style as `item`).

- [ ] **Step 1: Write failing tests**
  - `t_request_accepts_up_to_five_video_ids`: with a fake `open_url` capturing the URL, `request("videos", {"id": "aaaaaaaaaaa,bbbbbbbbbbb"}, KEY, open_url=fake)` puts `id=aaaaaaaaaaa%2Cbbbbbbbbbbb` in the URL. Six IDs raise `ValueError`, and so does `{"id": "aaaaaaaaaaa,"}`.
  - `t_newest_live_picks_the_later_start`: two live items started `2026-10-23T18:00:00Z` (`A`) and `2026-10-23T18:05:00Z` (`B`) → `("B…", title)`.
  - `t_newest_live_ignores_upcoming_and_ended`: one `upcoming` (no `actualStartTime`), one with `actualEndTime`, one live → the live one. All three non-live → `None`. An item whose ID is not in `ids` is ignored.
- [ ] **Step 2: Run** `python tests/test_audience_youtube.py`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `python tests/test_audience_youtube.py` and `python tools/lint.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(audience): query several YouTube videos and pick the newest live one`

### Task 2: Relay live-video tracker and channel-ID resolver

**Files:**
- Modify: `src/relay/racecast-feeds.py` (next to `yt_live_video_ids` ~3353; `BroadcastChatSupervisor` ~3737)
- Test: `tests/test_audience_follow_live.py` (new; load the relay module the same way `tests/test_console_gate.py` exposes it as `m`)

**Interfaces:**
- Produces:
  - `yt_live_video_lookup(channel, cookies, timeout=25, logger=None) -> list[str] | None`, with the same two yt-dlp calls as today. `None` means the lookup failed: the `/streams` JSON could not be read **and** the `/live` fallback gave no ID. `[]` means it succeeded with nothing live: the `/streams` JSON was read and the `/live` fallback gave no ID. Otherwise it returns the IDs.
  - `yt_live_video_ids(...)` keeps its signature and returns `yt_live_video_lookup(...) or []`.
  - `yt_channel_id(channel, cookies, timeout=25) -> str | None`. It runs `yt-dlp --flat-playlist --playlist-items 0 -J -- <channel_streams_url>` through `_yt_run_json` and returns its `channel_id` when it matches `UC[A-Za-z0-9_-]{22}`, else `None`.
  - `class LiveVideoTracker(cookies, lookup=yt_live_video_lookup, max_age=25, clock=time.monotonic)` with `lookup(channel) -> list[str] | None`. It returns the cached successful result when it is younger than `max_age`; otherwise it resolves now (one resolve at a time per channel, under a per-channel lock) and caches only successes. A failure returns `None` and keeps the old cache entry for at most `max_age`.
  - `BroadcastChatSupervisor(store, channel_source, cookies, interval=30, logger=None, tracker=None)`. With a tracker, `_desired` uses `tracker.lookup(channel) or []` instead of calling `yt_live_video_ids` directly.

- [ ] **Step 1: Write failing tests** (inject fake runners; no real yt-dlp)
  - `t_live_lookup_tells_failure_from_not_live`. Monkeypatch `_yt_run_json` and `subprocess.run`. The streams JSON has entries but none live, and `/live` returns code 1 → `[]`. The streams JSON is `None` and `/live` raises `TimeoutExpired` → `None`. The streams JSON has two live entries → both IDs. `yt_live_video_ids` returns `[]` for the failure case.
  - `t_channel_id_reads_yt_dlp_metadata`. `{"channel_id": "UC" + "a"*22}` gives that ID; `{"channel_id": "nope"}` and `None` give `None`.
  - `t_tracker_reuses_a_fresh_result_and_retries_a_failure`. A counting fake lookup plus a fake clock: two calls within 25 s mean one resolve, and a call at +26 s resolves again. A failing lookup returns `None` and is not cached as success.
  - `t_supervisor_uses_the_shared_tracker`. A supervisor with a fake tracker and one YouTube channel builds `_desired` keys from `tracker.lookup`, and `yt_live_video_ids` is never called (monkeypatched to raise).
- [ ] **Step 2: Run** `python tests/test_audience_follow_live.py`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the new file, `python tests/test_broadcast_chat.py` (and every existing test file that greps `yt_live_video_ids`/`BroadcastChatSupervisor`), and `python tools/lint.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(relay): share a quota-free live-video tracker and resolve channel IDs`

### Task 3: Follow mode in the YouTube connection service

**Files:**
- Modify: `src/scripts/audience_youtube_connection.py`
- Test: `tests/test_audience_youtube_connection.py`

**Interfaces:**
- Consumes: `audience_youtube.newest_live`, multi-ID `request` (Task 1). The callables below are provided by the relay in Task 4.
- Produces:
  - `Service(profile, runtime, cache=None, *, definitions=None, fetch=yt.request, clock=time.time, monotonic=time.monotonic, channels=None, live_lookup=None, channel_id_lookup=None)`.
    - `channels() -> list[str] | None` returns the YouTube row texts of the Channel tab, or `None` when there is no Channel tab.
    - `live_lookup(text) -> list[str] | None`.
    - `channel_id_lookup(text) -> str | None`.
    - Leaving any of them `None` makes follow mode unavailable.
  - Targets dict, as stored in the public file and in RAM:
    - Video mode (unchanged): `{"video": …, "channel": …}`. A missing `mode` means `"video"`.
    - Follow mode: `{"mode": "follow", "follow": {"channel": <row text ≤512>, "channelId": <UC id>, "title": <title>}, "video": None, "channel": {"id": <UC id>, "title": <title>}}`.
    - `_targets(data)` validates both shapes.
  - `command` verify in follow mode, with body `{"action": "verify", "mode": "follow", "followChannel": <text>, "apiKey": …, "expectedProfile": …}`. It raises `UserError` with exactly:
    - `"Follow mode needs the Sheet Channel tab"` when `channels()` is `None` or a callable is missing;
    - `"Choose a YouTube channel from the Sheet Channel tab"` when the text is not in `channels()`.
  - `_verify` in follow mode resolves the channel ID:
    1. `yt.channel_target(text)`. An `{"id"}` result is taken as is; `forHandle`/`forUsername` are resolved through `channels.list`.
    2. Else `channel_id_lookup(text)`.
    3. Else it fails with `"Use an @handle or channel ID in the Sheet Channel tab"`.
    
    Then `channels.list` by ID gives the title. If `live_lookup(text)` returns IDs, it makes one `videos.list` call to put `setup["live"] = {"id", "title"} | None` into the status. Not being live is not a failure.
  - `status()` adds:
    - `"mode"`: `"video"` or `"follow"`.
    - `"channels"`: the list from `channels()`, or `None`.
    - `"follow"`: `{"channel", "channelId", "state", "video"}`. `state` is one of `"unknown"`, `"live"`, `"not-live"`, `"lookup-failed"`, `"channel-changed"`, `"no-channel-tab"`; `video` is `{"id", "title"}` or `None`.
  - Follow cycle in `tick()`. It runs only while connected in follow mode and while a widget uses `likes` or `viewers`, at most every 30 s, before the video poll, with network calls outside `self.lock`.
    1. `channels()` is `None` → state `no-channel-tab`. The pinned row text is not in `channels()` → state `channel-changed`. In both cases set the video to `None` and invalidate likes/viewers with the reason `"Channel tab changed; verify again"` (`"Follow mode needs the Sheet Channel tab"` for no tab).
    2. `live_lookup(text)` returns `None` → state `lookup-failed` and the current video is kept.
    3. It returns `[]` → state `not-live`, the video is `None`, and likes/viewers are invalidated with `"Not live right now"`.
    4. It returns exactly the current video's ID → state `live`, unchanged.
    5. Otherwise one `fetch("videos", {"id": ",".join(ids[:5])}, key)` and `yt.newest_live`. `None` is handled like rule 3. A new ID replaces the video, sets `due["videos"] = 0` and invalidates likes/viewers with `"Waiting for YouTube metrics"`; state `live`.
  - The video poll in follow mode targets the followed video. When its `video_snapshot` state is `ended`, `not-live` or `inaccessible`, the followed video becomes `None` and the state becomes `not-live`.
  - Subscribers keep polling `targets["channel"]`, the pinned ID, in every state.

- [ ] **Step 1: Write failing tests.** Use fake `fetch` (records calls and returns canned payloads), fake `channels`/`live_lookup`/`channel_id_lookup` and fake clocks. Each test asserts:
  - `t_follow_verify_pins_the_channel_id`: `channels=lambda: ["@League"]`, `forHandle` resolves to `UC…a`. After verify + tick + activate, the public file holds `mode: "follow"` and `follow.channelId == "UC…a"`, and no video ID.
  - `t_follow_verify_rejects_missing_tab_and_unknown_row`: the two exact `UserError` texts.
  - `t_follow_verify_resolves_custom_urls_via_yt_dlp`: `"https://www.youtube.com/c/league"` with `channel_id_lookup` → `UC…b`. Returning `None` fails with the exact text.
  - `t_follow_cycle_switches_to_the_newer_live_video`. Lookup `["vidAAAAAAAA"]` → state live, then likes from that video. Lookup `["vidAAAAAAAA","vidBBBBBBBB"]` where B started later → video B, likes invalidated first, then likes with identity `youtube:video:vidBBBBBBBB:likes`.
  - `t_follow_mode_lookup_failure_keeps_or_waits`. With a current video, lookup `None` keeps it and polls it (state `lookup-failed`). Without a current video, `None` gives state `lookup-failed` and likes/viewers unavailable.
  - `t_follow_cycle_not_live_and_ended_hide_video_metrics`: lookup `[]` gives `not-live` with the reason `"Not live right now"`, and a `video_snapshot` state `ended` clears the video.
  - `t_follow_cycle_detects_a_changed_channel_tab`: `channels()` drops the row, giving state `channel-changed`, likes unavailable, subscribers still polled for the pinned ID.
  - `t_follow_mode_survives_restart`: a new `Service` over the same profile/runtime dirs is connected in follow mode and follows on its first tick.
  - `t_follow_mode_without_key_needs_rekey`: the public follow file without the runtime key gives `connected False` and an error containing `REKEY`, and makes no fetch calls.
  - `t_video_mode_unchanged`: the existing video-mode tests still pass, with no `mode` key in old files.
- [ ] **Step 2: Run** `python tests/test_audience_youtube_connection.py`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces (state under `self.lock`, network outside it, generation fencing as `_poll` does).
- [ ] **Step 4: Run** the file, all `tests/test_audience_*.py`, and `python tools/lint.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(audience): follow the Sheet channel's live broadcast`

### Task 4: Relay wiring

**Files:**
- Modify: `src/relay/racecast-feeds.py` (main ~14047-14060 Channel source, ~14351 supervisor, ~14391 `audience_youtube_connection.Service(...)`)
- Test: `tests/test_audience_follow_live.py`

**Interfaces:**
- Consumes: Task 2 tracker/resolvers and Task 3 Service kwargs.
- Produces: in `main`:
  - `live_channels = channel_source or (ChannelSource(channel_csv_url, os.path.join(runtime, "channel.cache.txt")) if channel_csv_url else None)`. This source is used only for the audience; `channel_source` and its broadcast-chat/`event_platform` uses stay unchanged.
  - `live_tracker = LiveVideoTracker(cookies) if live_channels else None`, passed to `BroadcastChatSupervisor(..., tracker=live_tracker)`.
  - The YouTube Service gets:
    - `channels=` a callable that refreshes `live_channels` at most every 30 s (monotonic) and returns `[c for p, c in live_channels.get() if p == "youtube"]`;
    - `live_lookup=live_tracker.lookup`;
    - `channel_id_lookup=lambda text: yt_channel_id(text, cookies)`.
    
    All three are `None` when `live_channels` is `None`.
  - Pull the construction into a pure helper `audience_follow_hooks(live_channels, live_tracker, cookies, clock=time.monotonic) -> dict` returning the three kwargs, so it is testable.

- [ ] **Step 1: Write failing tests**
  - `t_follow_hooks_filter_youtube_rows_and_refresh_rarely`. Use a fake source with `[("youtube","@a"),("twitch","x"),("youtube","UC…")]` and a counting `refresh`. `channels()` returns the two YouTube rows, and two calls within 30 s mean one refresh.
  - `t_follow_hooks_are_none_without_a_channel_tab`: `audience_follow_hooks(None, None, None)` returns all `None`.
  - `t_follow_wiring_without_broadcast_chat`: source-level assertion that `main` builds `live_channels` from `channel_csv_url` independently of `args.no_broadcast_chat` (assert the helper is called with a source when `channel_source` is `None`). Keep it as a pure-function test on a small extracted `live_channel_source(channel_source, channel_csv_url, runtime) -> ChannelSource | None`.
- [ ] **Step 2: Run** `python tests/test_audience_follow_live.py`. Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the file, `python tests/test_audience_integration.py`, `python tools/lint.py`, then `python tools/e2e.py` (synthetic). Expected: PASS.
- [ ] **Step 5: Commit** `feat(relay): wire follow mode to the Channel tab and the live tracker`

### Task 5: Dialog, docs, screenshots

**Files:**
- Modify: `src/ui/control-center.html` (YouTube dialog markup ~855-873, JS `youtubePoll`/`youtubeAction` ~3149-3244)
- Modify: `tools/e2e-youtube-audience.py` (follow-mode flow with injected provider fixtures; `--shots`)
- Modify: `src/docs/wiki/HUD-Overlays.md` (YouTube section), `src/scripts/CLAUDE.md` ("Audience provider connections" paragraph)
- Modify: `src/docs/wiki/images/cc-audience-youtube.png` and `src/docs/slides/assets/img/cc-audience-youtube.png`
- Test: `tests/test_audience_youtube_ui.py`

**Interfaces:**
- Consumes: the Task 3 status fields `mode`, `channels`, `follow` and the verify body `{mode: "follow", followChannel}`.
- Dialog:
  - A mode choice (radio) `#youtube-mode-follow` / `#youtube-mode-video` with the labels **Follow the live broadcast of the Sheet channel** and **Fixed video**.
  - Follow shows `<select id="youtube-follow-channel">` filled from `status.channels`. When `channels` is `null` the follow radio is disabled, with the note "Follow mode needs the Sheet Channel tab".
  - The status lines show `Following: <title> [<channelId>]` and `Live: <video title> [<id>]`, `Not live right now`, `Lookup failed; keeping the last video`, or `Channel tab changed; verify again`.
  - Fixed video keeps today's fields and adds the hint text "Each new live broadcast has a new video ID: enter it, verify and activate before going live."
  - The key hint links to the wiki section anchor `HUD-Overlays#youtube-data-api-key`. Use the GitHub wiki URL pattern already used elsewhere in the Control Center; grep for an existing wiki link.
  - Editing the mode or the channel select disables Activate, like the existing fields do.
- Wiki: a new subsection `### YouTube Data API key` with the steps from the spec (project, enable API v3, Credentials → Create credentials → API key, restrict to YouTube Data API v3), the quota line from the spec, follow mode (Channel tab channel, newer of two lives, not live means empty, Channel tab change means verify again, net goals need a new baseline after a switch) and the fixed-video per-broadcast note.

- [ ] **Step 1: Write failing tests**
  - `t_youtube_dialog_offers_follow_mode`: the page source contains `id="youtube-mode-follow"`, `id="youtube-follow-channel"`, the exact fixed-video hint sentence and `HUD-Overlays#youtube-data-api-key`. `youtubeAction` sends `mode` and `followChannel`.
  - `t_wiki_documents_the_api_key_steps`: `HUD-Overlays.md` contains `### YouTube Data API key`, `YouTube Data API v3`, `Create credentials` and `new video ID`.
- [ ] **Step 2: Run** `python tests/test_audience_youtube_ui.py`. Expected: FAIL.
- [ ] **Step 3: Implement** dialog + wiki + CLAUDE.md paragraph. Extend `tools/e2e-youtube-audience.py` with a follow-mode pass (fixture status with `channels` and `follow` states). Run `runtime/pw-venv/Scripts/python.exe tools/e2e-youtube-audience.py` and then `--shots`.
- [ ] **Step 4: Visual check.** Read the regenerated `cc-audience-youtube.png` back; run the `ui-visual-verification` skill and record the marker for `src/ui/control-center.html`.
- [ ] **Step 5: Gates and commit.** Run `python tools/run-tests.py`, `python tools/lint.py` and `python tools/build.py`, all green. Commit `feat(ui): follow-live mode and API key how-to in the YouTube connection dialog`.

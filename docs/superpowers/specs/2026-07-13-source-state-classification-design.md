# Source-state classification + notification tuning (#495 core)

**Date:** 2026-07-13
**Issue:** [#495](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/495)
**Scope:** `src/relay/racecast-feeds.py` only (relay). This is the **core** slice of #495 —
classification + notification. The **on-air slate** (extends the #378 auto-failover) and a
**failover/backup source** (overlaps #493) are a deliberate **follow-up**, not this PR.
Single PR into `main`.

## Problem

From the N24 post-event logs, two source-not-live conditions were handled as generic
drops:
- **Twitch not live yet** at handover — `No playable streams found on this URL` (stint 6
  kekko_simracing, ~5 min until the streamer went live).
- **YouTube live-event ENDED** — `This live event has ended` ×17 (stint 9's PS-over-WiFi
  upload collapsed so hard YouTube ended the broadcast; 4 min of black).

The relay retries both (correct), but the crew sees only a generic "stuck connecting" /
"lost the live stream" with no signal of **why**, and the feed-churn `@here` fired twice —
both during these already-known-unstable sources (alarm fatigue).

## What already exists (so we don't rebuild it)

- **Severity is already gentle for a never-served source.** `feed_health_state` returns
  `"connecting"` (yellow/DEGRADED, never red) when `served_ok` is False — a source that
  never delivered a stable picture (a not-live-yet handover) is *already* not a red "lost
  the live stream". So #495's core adds a **distinct reason**, not a new severity.
- **Churn `@here` already has a per-feed cooldown** (`FEED_CHURN_*`, `feed_recovery_churn`).
  #495 adds a **classification-aware suppression** on top.
- **The slate already exists** as `RACECAST_AUTO_FAILOVER` (#378, Intermission scene on a
  confirmed on-air loss). Extending it to the not-live-yet case is the deferred follow-up.

## Design

### 1. Pure classifier

```python
def classify_source_state(text):
    """Classify a feed's yt-dlp/streamlink diagnostic *text* into a source-not-live
    state, or None for a generic drop/error. Pure → unit-tested with real log lines.

    "not_live_yet" — the source is offline / has not started: Twitch "No playable
                     streams found", yt-dlp "not currently live" / "is not currently
                     live", the "not live?" resolve fallback.
    "ended"        — the source's live broadcast is over: YouTube "This live event
                     has ended".
    None           — anything else (429/403/network/generic) — unchanged behaviour."""
```

Case-insensitive substring matching against a small, documented signature table. A 429/403
or an unknown error returns None (the existing drop path is untouched).

**Live status after a format error (#621).** A YouTube broadcast in Post-Live Manifestless
mode offers only DASH video-only/audio-only formats, so yt-dlp fails with `Requested format is
not available`, the same text a logged-out cookie jar produces (#615). The text alone cannot
tell them apart. Only on that error, `resolve_hls` makes a second yt-dlp call
(`ytdlp_live_status_cmd`: `--skip-download --ignore-no-formats-error --print "rcs
%(live_status)s"`) and appends ` (live_status <status>)` to the error. The signature table maps
`live_status post_live` / `live_status was_live` to `ended` and `live_status is_upcoming` to
`not_live_yet`; `is_live` stays a generic drop. The flag stays off the resolve itself: yt-dlp
routes every playability reason (bot check, rate limit, "This live event has ended") through
`raise_no_formats`, which the flag downgrades to a warning that `--no-warnings` hides. A
successful resolve is not reclassified: a `was_live` recording that resolves is still served.

### 2. Feed carries `source_state`

- New `Feed.source_state` field (default `None`).
- **YouTube:** after `hls, err = resolve_hls(...)`, when `err` is set →
  `self.source_state = classify_source_state(err)`.
- **Twitch:** `_observe_streamlink_line` matches `No playable streams found` →
  `self.source_state = "not_live_yet"`.
- **Cleared to `None`** on a successful serve (the first-byte / served-ok path), so a
  source that recovers stops reporting a stale state.

### 3. Distinct `/status` + health reason

- `/status` per-feed block gains `"source_state": self.source_state`.
- `_health_facts` carries a `feed_source_states` map (`{feed_name: source_state}`) into
  `aggregate_health` — the existing `feeds_down` / `feeds_connecting_long` name lists are
  unchanged; the map only **enriches the reason text**:
  - A `feeds_connecting_long` feed with `source_state == "not_live_yet"` →
    **"Feed X — commentator source not live yet (connecting)"** (stays yellow/DEGRADED).
  - A `feeds_down` feed with `source_state == "ended"` →
    **"Feed X — source's live stream ENDED (no auto-recovery — switch source)"**
    (stays **red** — a genuine on-air loss the crew must act on — but the reason makes
    clear that retrying the dead URL is futile).
  - No `source_state` → the current generic reason, unchanged.

### 4. Churn `@here` dampening

In the churn-recovery path (`feed_recovery_churn` / the `_record_feed_recovery` notify
decision), **suppress the `@here`** when the recovering feed's `source_state` is
`not_live_yet` **or** `ended` — a source that is offline / has ended and keeps flapping
while the relay retries is *expected* churn, not a relay fault. The per-feed cooldown stays
for genuine network churn (`source_state is None`). The feed still surfaces its state once
via the distinct health reason (§3); it just doesn't spam churn `@here`.

### 5. Tests

- Pure `classify_source_state`: real N24 lines — `"No playable streams found on this URL:
  twitch.tv/kekko"` → `not_live_yet`; `"This live event has ended"` → `ended`; yt-dlp
  `"... is not currently live"` → `not_live_yet`; a `429 Too Many Requests` / generic line
  → `None`.
- Health reasons: a connecting feed with `not_live_yet` → yellow, the distinct reason, and
  **no red**; a down feed with `ended` → red, the distinct reason. (Pure `aggregate_health`
  with synthetic facts incl. `feed_source_states`.)
- Churn: `_record_feed_recovery` / the churn-notify decision does **not** fire `@here` for a
  feed whose `source_state` is `not_live_yet` or `ended`; still fires for `None` (genuine
  churn), respecting the cooldown.

## Explicitly out of scope (follow-up)

- **On-air slate** for the not-live-yet / ended case — extend the #378 auto-failover
  (Intermission scene) so an offline/ended source shows an intentional "commentator
  connecting / source offline" slate instead of black/frozen. Touches the live OBS path;
  its own design dialogue (which scene, the return path, interaction with #378's
  already-failed-over latch).
- **Failover / backup source** (a dead primary → backup URL / hold previous / POV) —
  overlaps #493.

## Acceptance criteria (the #495 items this PR closes)

- [ ] Twitch-offline and YouTube-ended are classified as distinct source states
      (`not_live_yet` / `ended`), not generic drops — exposed on `/status` per feed.
- [ ] A not-yet-live source at handover does not page the same alarm severity as a lost
      live stream (stays yellow/DEGRADED with a distinct reason; `ended` stays red with a
      distinct, actionable reason).
- [ ] `@here` churn notifications are suppressed for a feed classified `not_live_yet` /
      `ended` (no spam for an expected-unstable source); genuine churn still notifies.
- [ ] Unit tests for the classifier, the health-reason enrichment, and the churn-suppression
      decision (pure helpers).

_(The remaining #495 ACs — the on-air slate and the configurable fallback source — are the
deferred follow-up.)_

# On-air-aware SPLIT audio — design

**Issue:** #534.

**Status:** design approved, ready for an implementation plan.

**Source:** Suzuka 8h post-event analysis (2026-07-18). At the stint 2→3 handover
the splitscreen audio was backwards — the incoming feed was audible and the
on-air commentator was muted for ~62 s. The stint 3→4 handover was fine.

## Problem

The `SPLIT` control (Director Panel macro and the Companion "Split Scene" button)
hard-codes its audio: **unmute `Feed A`, mute `Feed B` + `Discord Audio Capture`**.
That is correct only when the outgoing/on-air feed is A. Proven from the OBS
scene log:

| Handover | On air at SPLIT | Hardcoded effect | Result |
|---|---|---|---|
| 1→2 | Feed **A** | unmute A | ✅ |
| **2→3** | Feed **B** | mutes the on-air B | ❌ backwards |
| 3→4 | Feed **A** | unmute A | ✅ |

Odd→even handovers (A on air) are correct; even→odd (B on air) mute the live
commentator. The fix: the SPLIT audio must follow **whichever feed is on air**,
resolved from a single source of truth.

## Approach: resolve on-air server-side, one relay endpoint

The relay already knows the on-air feed (`Relay.live_feed()` → `"A"`/`"B"`, the
lower stint index) and owns the OBS-WebSocket connection
(`_obs_ws.set_input_mute`). Rather than duplicate the on-air logic in panel JS
(and leave Companion — which cannot branch on live state — unfixable), a single
relay endpoint resolves the on-air feed and applies the mutes. Both callers
invoke it.

### Audio target rule (the bug fix)

Pure function `split_audio_targets(live_feed)` → `(unmute, mute)`:

- `"A"` → unmute `"Feed A"`, mute `["Feed B", "Discord Audio Capture"]`
- `"B"` → unmute `"Feed B"`, mute `["Feed A", "Discord Audio Capture"]`

i.e. unmute the on-air feed, mute the off-air feed and the interview/Discord bus —
exactly the current SPLIT intent, but dynamic. The feed input names
(`Feed A`/`Feed B`/`Discord Audio Capture`) match the panel `CONFIG` macro strings
and are defined once, server-side.

## Components

### 1. Pure targets (unit-tested)

`split_audio_targets(live_feed)` in the relay module — a pure `(unmute, mute)`
resolver; both handover directions unit-tested (the regression guard for the
Suzuka bug). The input-name constants live next to it.

### 2. Relay handler `_apply_split_audio()`

Reads `relay.live_feed()`, computes targets via `split_audio_targets`, and applies
them best-effort through `_obs_ws.set_input_mute` — the same contract as the
existing `/obs/audio` handler: OBS unreachable (`_obs_ws is None` /
`set_input_mute` returns falsey) → HTTP 503 with a note; never raises. Returns
`{ok, live, unmute, mute, note}`.

### 3. Two routes, one handler (transport per caller)

- **`POST /obs/split-audio`** under the existing `/console` mount → director-gated
  by `console_policy` (capability `obs`, like the other `/obs/*` controls) and
  reachable over Funnel. This is the Director Panel's path.
- **`GET /obs/split-audio`** on the tailnet root → for the Companion button,
  mirroring the existing `/obs/flag/*` GET precedent. Tailnet is the trust
  boundary; this route is **never** Funnelled.

Both dispatch to `_apply_split_audio()`. No new public/Funnel surface beyond the
existing `/console` mount; OBS-WebSocket is never funnelled.

### 4. Director Panel

The `SPLIT` macro keeps its scene switch + show/hide (`Splitscreen`, show both
feeds) and its Race-Control stamp; its static `unmute`/`mute` arrays are removed
and replaced by a single call to `POST …/obs/split-audio` (through the panel's
existing relay-API base, so it works locally and over Funnel). No on-air logic in
panel JS. `STINT A`/`STINT B` macros are unchanged — they explicitly pick a feed.

### 5. Companion "Split Scene" button

Keeps its scene switch (Companion's OBS module) and gains a Generic-HTTP **GET**
to `…/obs/split-audio` for the audio, replacing any static per-input mute actions
on that button. Authored via the `companion-buttons` skill; the wiki button-board
screenshots are regenerated via `companion-screenshots` in the same change.

## Interactions / non-goals

- The director-only HUD `racecontrol` banner (Setup tab) is unrelated and
  untouched; the `race_control` crew role is unrelated.
- Qualifying mode: a single feed lands on A, so `live_feed()` returns `"A"` and
  SPLIT audio is a no-op-correct (unmute A / mute B); no special-casing.
- No confirm phrase — SPLIT is a quick switch, director-gated like the other
  `/obs/*` controls.

## Testing

- **Pure (CI, stdlib):** `split_audio_targets("A")` and `("B")` return the correct
  `(unmute, mute)` — both directions (the even→odd case is the fixed bug).
- **Endpoint:** with a stub relay (`live_feed` → `"B"`) and a recording `obs_ws`,
  the handler calls `set_input_mute("Feed B", False)`, `set_input_mute("Feed A", True)`,
  `set_input_mute("Discord Audio Capture", True)`; OBS-unavailable → 503.
- **Panel:** the SPLIT macro issues one `/obs/split-audio` call and no per-input
  mute calls (light DOM/handler check consistent with existing panel tests).

## Out of scope

The stutter/prebuffer work (#533, merged) and the other Suzuka findings
(#535–#538).

## Follow-up: visibility moved to the relay too (#591)

The Splitscreen's feed visibility now resolves on the relay as well. The pure
`split_state_intents(live, do_cut, slots=None)` in `src/scripts/obs_ws.py` sits next
to `feed_state_intents` and returns show / unmute / mute (/ cut) intents. `slots`
maps A/B to `(scene item, [audio inputs])`, so a slot can be heard through more than
one input (a local capture slot plus the commentary microphone, #590).
`split_audio_targets` is now its audio half. `GET`/`POST /obs/split` applies the
whole state (both feeds visible, audio as above) one intent at a time, continuing
past a failed intent and reporting it. The Companion `SPLIT` and `Split Scene`
buttons, the Director Panel `SPLIT` macro and the smoke-test rundown call it and
name no source. They still cut to `Splitscreen` themselves, so the cut survives a
relay outage. `/obs/split-audio` (sections 2 and 3) stays for boards imported
before this change and keeps its payload.

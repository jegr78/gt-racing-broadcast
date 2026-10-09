# Director-Panel Broadcast Part Control — Design

**Status:** Approved (brainstorm), ready for implementation plan
**Date:** 2026-07-04
**Related:** #395 (cloud-producer spike), Producer tab / `stream-target` (`src/scripts/producer.py`,
`src/scripts/stream_target.py`, `racecast.py:_apply_stream_target`), `/obs/*` relay controls.

## Goal

Make the **Director Panel** the single control surface for a broadcast's **Parts** on the
GCP cloud producer, replacing the RustDesk-based producer-takeover workflow. From the panel
the director can, per Part: **go live**, **end the Part**, and **continue with the next
Part** — each change to the public live state gated by an explicit **typed confirmation**.
Plus the minimal changes that let `racecast event start` bring OBS + Discord up over plain
SSH, so no RustDesk session is needed on event day.

**Context / motivation.** With the producer moving fully into GCP, every event runs on one
long-lived cloud box — there is only ever *this* producer, so the multi-producer takeover
handover disappears. Long broadcasts are still split into **Parts** (1 Part ≤ 10 h; 2 Parts
≤ 18 h; 3 Parts for the ~25 h, 24-hour races), each a **separate YouTube broadcast with its
own stream key**. The director already has stream keys + the Part breakdown maintained in the
Sheet's `Producer` tab, and the panel already starts/stops the OBS stream via obs-websocket.
This design closes the gap: the director drives the Part boundaries directly, safely, from
the panel — without touching the producer machine's desktop.

## Background — what already exists (reused, not rebuilt)

- **Start/stop the OBS stream from the panel.** `POST /obs/stream {"on": bool}` →
  `obs_ws.set_stream()` (issues `StartStream`/`StopStream`, idempotent). The panel renders a
  `GO LIVE / ● LIVE / RECONNECTING` button (`#obsStreamBtn`) driven by polling `/obs/state`,
  whose response already carries a `stream` block (`active`, `reconnecting`, `timecode`).
- **Part → stream key resolution.** The `Producer` Sheet tab (`Part | Producer | MagicDNS |
  Stream Key`), where `Stream Key` is a **reference label** (`key1`, `key2`), never the real
  key. `src/scripts/producer.py` (`parse_producer_rows`) and `src/scripts/stream_target.py`
  (`resolve_part_ref`, `event_platform`, `parse_stream_key_response`) are pure, tested
  helpers. `racecast.py:_apply_stream_target(part)` resolves Part → ref → fetches the real
  key via the `get_stream_key` webhook (`SHEET_PUSH_URL`) → `obs_ws.set_stream_service()`.
  This is CLI-only today (`racecast obs stream-target <part>`) — **not reachable from the
  panel**.
- **`obs_ws.set_stream_service()`** issues `SetStreamServiceSettings` and **refuses while
  streaming** ("stop the broadcast before changing the stream target"). This is a hard
  constraint the state machine is built around: a retarget only happens in the not-live
  window.
- **Persisted server-side state pattern.** `TimerStore` / `ChatStore` / `CueStore` —
  lock-guarded JSON at `runtime/<profile>/*.json`, loaded on construction, best-effort save.
- **Sheet-tab reader pattern.** `ChannelSource` (fetch + lock + cache; pure parsing in a
  separate module). No `ProducerSource` exists yet — the Producer tab is read on demand.
- **Director gating + Funnel.** `/obs/*` is director-gated in `console_policy.py`
  (`p[0] == "obs"` → `Requirement(DIRECTOR, False)`) and reached from the funnelled
  `/console/panel` via the injected `RC_API_BASE` shim — no separate public mount.

## Non-goals

- **No intermission/standby scene automation.** Between Parts the stream is intentionally
  offline; the director switches scenes with the existing scene macros. The relay owns only
  start/stop/retarget.
- **No new stream-key storage.** Keys stay in Apps Script Script Properties, fetched on
  demand via the existing `get_stream_key` webhook. No key ever reaches the browser, a log,
  or `/parts/data`.
- **No producer-takeover changes.** The tailnet/Funnel `event takeover` paths are untouched;
  this design is the single-producer replacement for *that box's* day-of control, not a
  rewrite of handover.
- **No per-league Discord voice auto-join.** The headless work gets the Discord *app*
  running; joining a voice channel remains whatever the crew does today.

## Architecture

**The one architectural choice — server-side atomic Part actions.** The panel sends *one*
high-level request per Part boundary (with the typed-confirm intent); the **relay** performs
the whole sequence — resolve Part → key-ref, fetch the real key, `set_stream_service`,
`StartStream`/`StopStream`. The browser never sees the key, the sequence is atomic (it can't
half-complete across a flaky tablet link), and it reuses the existing pure helpers.

*Rejected:* client-orchestrated multi-step (panel calls `set_stream_service`, then
`StartStream` separately) — that pushes the key toward the browser and can leave a broadcast
half-configured if the second call is lost.

### Components

1. **`ProducerSource`** (relay, `src/relay/racecast-feeds.py`) — a Sheet-tab reader for the
   `Producer` tab, mirroring `ChannelSource`: `_fetch_text` (gviz CSV from `SHEET_ID` +
   `Producer`), `refresh()` calling the pure `producer.parse_producer_rows`, lock-guarded
   `get()`, optional last-good cache. Polled like the other sources. Gives the relay the
   ordered Part list live. Flag `--producer-tab` (default `Producer`), and disabled (empty
   list) when the tab is absent/unreachable — never raises.

2. **`PartStore`** (relay) — a small persisted pointer at `runtime/<profile>/part.json`,
   modeled on `TimerStore`: lock-guarded, loaded on construction, best-effort save.
   Canonical state: `{"index": N, "live": bool}` where `index` is 1-based into the Producer
   row order. Type-checked adoption of known keys on load (a hand-edited file never crashes
   later). `live` is a **written record only** — the authoritative live indicator is OBS's
   real stream status (see below).

3. **Relay stream-target apply** (`src/relay/racecast-feeds.py`) — a small relay-side
   function mirroring `racecast.py:_apply_stream_target`, but running inside the relay:
   resolve Part label → ref via `stream_target.resolve_part_ref` (from `ProducerSource`
   rows), fetch the real key by POSTing `{"action":"get_stream_key","ref":ref}` to
   `SHEET_PUSH_URL` (the relay's existing webhook-POST path — the relay is exempt from the
   `http_util` UA guard and already posts to this webhook for timer/setup), parse via
   `stream_target.parse_stream_key_response`, then `obs_ws.set_stream_service(platform,
   key)`. Platform from the `Channel` tab via `event_platform` (the relay already reads the
   Channel tab for broadcast chat). Imports the pure `producer` / `stream_target` modules
   (same precedent as importing `broadcast_chat`). **The key is never logged or returned.**

4. **`/parts/*` endpoints** (relay) — director-gated, mutating actions require the intent
   phrase:
   - `GET /parts/data` — panel view model: `{"parts": [{"index","label","producer"}...],
     "index": N, "live": bool, "platform": "youtube|twitch|null", "count": M,
     "next_index": N+1|null, "enabled": bool}`. **No stream keys or refs.** `live` here is
     reconciled from OBS's actual stream status when reachable, else the stored flag.
     `enabled=false` when no Producer parts exist (panel falls back to plain GO LIVE).
   - `POST /parts/start {"index": N, "intent": "<phrase>"}` — go live with Part N. Sequence:
     (a) refuse if OBS is already streaming; (b) refuse if `N` ≠ the expected next Part
     (stale-tablet guard); (c) validate `intent` == `parts_intent_phrase("start", N)`;
     (d) resolve + fetch key + `set_stream_service`; (e) `StartStream`; (f) persist
     `{index: N, live: true}`. **Never `StartStream` without a confirmed applied target**
     (fail-safe: if the key fetch or `set_stream_service` fails, do not start).
   - `POST /parts/end {"intent": "<phrase>"}` — end the running Part: validate `intent` ==
     `parts_intent_phrase("end", index)`; `StopStream`; persist `live: false`, `index`
     unchanged.
   - All return `503` with a note when OBS is unreachable (same contract as `/obs/*`), `409`
     on the stale-index / already-streaming guards, `403` on a bad/missing intent phrase.

5. **Intent phrase (anti-accident layer)** — a pure helper `parts_intent_phrase(action,
   index)` → deterministic phrase, e.g. `"START PART 2"` / `"END PART 2"`. The panel's modal
   requires the director to type exactly this phrase; the panel sends it as `intent`; the
   server recomputes and compares. This is **not** an auth mechanism (the director token
   already proves authorization) — it exists solely so a stray tap or a blind scripted POST
   cannot change the live state. Case/whitespace-normalized comparison.

6. **Authorization & Funnel.** Add `p[0] == "parts"` → `Requirement(DIRECTOR, False)` to
   `console_policy.py` (mirroring `/obs/*`). Reachable from the funnelled `/console/panel`
   through the same `RC_API_BASE` shim — **no new public surface**; OBS-WebSocket is still
   never funnelled. The intent phrase is enforced in the handler, not the policy layer.

7. **Director Panel Part control** (`src/director/director-panel.html`). The existing
   generic stream button becomes **Part-aware** when `GET /parts/data` reports
   `enabled: true`:
   - Shows `Part N of M` + the on/offline state (from OBS's real stream status, polled as
     today via `/obs/state`, cross-checked with `/parts/data`).
   - Offline + a Part available → `Start Part N` button; live → `End Part N` button; after
     End with a next Part → `Start Part N+1`; last/only Part ended → "event complete", no
     Start.
   - Every Start/End opens the **typed-confirmation modal** naming the exact Part and its
     consequence (see Section "Confirmation UX"); on confirm it POSTs to `/parts/start|end`
     with the typed `intent`.
   - **Backward-compatible fallback:** when `enabled: false` (no Producer tab), the panel
     keeps today's plain `GO LIVE / ● LIVE` button wired to `/obs/stream` (a simple
     `confirm()` on stop, unchanged). Existing leagues are unaffected — racecast is released.

8. **Headless bring-up (SSH, no RustDesk).** Two belt-and-suspenders changes plus the reset:
   - **`event start` provides a display** (`src/scripts/event.py` launch path): on Linux,
     when launching a GUI app (OBS/Discord) and `DISPLAY` is unset (a bare SSH shell), set
     `DISPLAY=:0` and discover `XAUTHORITY` (the login user's `~/.Xauthority`, else the
     lightdm-managed path) so the app opens into the running autologin xfce session.
     Overridable via `RACECAST_DISPLAY`. Best-effort and idempotent — if the app is already
     running, `event start` still just reports "already running". A pure, unit-tested helper
     resolves the launch environment; the subprocess spawn stays where it is.
   - **Autostart at boot** (`tools/cloud/provision.py`, maintainer): xfce autostart
     `.desktop` entries for OBS + Discord, so they come up with the session at boot and
     `event start` finds them running. (Both mechanisms coexist: autostart is the default
     state, the `DISPLAY` fix is the on-demand path.)
   - **Index reset** (`event start`): writes `part.json = {index: 1, live: false}` — the one
     reliable reset point (every event begins with `event start`; last-Part-stop / `event
     stop` cannot be reliably detected). New flag **`--part N`** initializes `{index: N,
     live: false}` for mid-event recovery (mirrors the existing `--stint N`). `event stop`,
     a bare relay restart, and profile switches do **not** touch the index.

## State machine

State `part.json`: `{"index": N, "live": bool}`. `event start` → `{1, false}` (or `{N,
false}` with `--part N`).

```
event start            → {1, false}     Panel: "Part 1 of 3 — OFFLINE"          [Start Part 1]
Start Part 1 (typed)   → set_service(key1) → StartStream → {1, true}
                                          Panel: "● LIVE — Part 1 of 3"          [End Part 1]
End Part 1  (typed)    → StopStream → {1, false}
                                          Panel: "Part 1 ended — Next: Part 2"   [Start Part 2]
Start Part 2 (typed)   → set_service(key2) → StartStream → {2, true}
                                          Panel: "● LIVE — Part 2 of 3"          [End Part 2]
   … Part 3 …
End Part 3  (typed)    → StopStream → {3, false}
                                          Panel: "Part 3 ended — event complete" (no Start)
```

- **Part 1 start** is the pure "nothing to stop" case: service + `key1` are applied while
  offline (allowed), *then* the stream goes live — one atomic, confirmed action.
- **Single-Part event / last Part:** `index` stays at the last row; End stops, no Start is
  offered afterward ("letzter Part = nur stoppen").
- **`next_index`** in `/parts/data` = `index + 1` if `< count`, else `null`.

## Confirmation UX

The typed-confirmation modal (director panel), fired by every Start/End:

```
⚠ END PART 2 of 3
This STOPS the live YouTube broadcast (Part 2). Viewers see the stream end.

Type  END PART 2  to confirm:
[________________]        [Cancel]  [Confirm]
```

- The `Confirm` button stays disabled until the typed text matches the phrase (client-side),
  and the server re-validates the `intent` (authoritative). Both use the same normalization.
- Start uses `START PART N` with a "This GOES LIVE on YouTube (Part N)" message.

## Data flow — "Start Part 1"

1. Director clicks `Start Part 1` → modal → types `START PART 1` → Confirm.
2. Panel `POST /console/parts/start {"index":1,"intent":"START PART 1"}` (director token).
3. Relay: `decide()` → director OK. Handler: OBS not streaming ✅; `index==expected` ✅;
   `intent==parts_intent_phrase("start",1)` ✅.
4. Relay: `resolve_part_ref(producer_rows, "Part 1")` → `key1`; POST `get_stream_key` →
   real key; `set_stream_service("youtube", key)` (allowed — offline).
5. Relay: `StartStream`; persist `{1, true}`; return status.
6. Panel poll (`/parts/data` + `/obs/state`) flips to `● LIVE — Part 1 of 3` / `End Part 1`.

## Error handling

- **OBS unreachable** → `503 {"ok":false,"note":...}` (same as `/obs/*`); panel surfaces it,
  no state change.
- **Producer tab empty/missing** → `/parts/data` `enabled:false`; panel shows plain GO LIVE.
- **`get_stream_key` webhook fails / empty key** → `start` returns an error and **does not
  `StartStream`** (fail-safe: never go live without a confirmed target). Panel shows the
  error; state stays offline.
- **`set_stream_service` refused (OBS already streaming)** → guard (a) returns `409` with a
  clear "end the current Part first" note; the UI never offers Start while live anyway.
- **Stale tablet (wrong `index`)** → guard (b) returns `409` naming the expected next Part.
- **Bad/missing intent phrase** → `403`; no state change.
- All relay obs_ws calls remain best-effort (never raise) — a failure is one logged note,
  never a relay crash.

## Security

- **Auth:** director capability (existing token), unchanged.
- **Anti-accident:** server-validated typed intent phrase on every live-state change.
- **Stream key confidentiality:** fetched server-side, applied over localhost obs-websocket,
  never in the browser, `/parts/data`, logs, or print paths (preserves the existing
  Script-Properties model).
- **Funnel boundary:** `/parts/*` reachable only through the existing `/console` mount, same
  as `/obs/*`; no new public surface; OBS-WebSocket never funnelled.

## Backward compatibility

- Leagues **without** a Producer tab: `/parts/data` reports `enabled:false`, the panel keeps
  today's plain GO-LIVE button and behavior. No profile migration needed.
- `part.json` is created lazily; a missing file = `{1, false}`.
- No CLI flag is removed or renamed. `racecast obs stream-target <part>` stays (still useful
  from a shell). New surface only: `event start --part N` and the `/parts/*` endpoints.

## Testing

Pure-first, stdlib runnable-script tests (new `tests/test_parts.py` unless noted):

- `parts_intent_phrase(action, index)` — exact phrases + normalization (case/whitespace).
- `PartStore` — default `{1,false}`, load with type-checked adoption, best-effort save,
  `event start` reset, `--part N` init (model on `tests/test_timer.py`).
- `ProducerSource` parsing/tolerance — reuses `producer.parse_producer_rows` (already tested
  in `tests/`); add a Source-level fetch/lock/empty-tab test.
- View-model builder for `/parts/data` — pure function mapping `(producer_rows, part_state,
  obs_stream_status)` → the response dict, incl. `enabled`, `next_index`, last-Part.
- `/parts/*` endpoint routing + guards (OBS-unreachable 503, stale-index 409,
  already-streaming 409, bad-intent 403, happy path) — relay endpoint tests.
- `console_policy`: `parts` → `Requirement(DIRECTOR, False)` (extend `tests/test_console.py`).
- Headless launch-env resolver (pure): `DISPLAY` unset on Linux → `DISPLAY=:0` +
  discovered `XAUTHORITY`; `RACECAST_DISPLAY` override; DISPLAY already set → untouched;
  non-Linux → untouched (extend `tests/test_event.py`). Cross-platform: build any fixed
  paths with explicit `/`, never `os.path.join`, for the Windows runner.

## Docs / wiki

- **Refresh `src/docs/wiki/images/director-panel.png`** in the same change (hard rule: any
  visible Director Panel change re-captures its screenshot — `wiki-screenshots` skill, demo
  profile + `obs-sim`).
- Update `src/docs/wiki/Run-an-event.md` (Part-driven day-of flow from the panel; SSH-only
  bring-up) and `tools/cloud/README.md` (autostart entries; `event start` over SSH).
- `Sheet-Webhook.md` already documents `get_stream_key`; add a note that the relay now uses
  it for panel-driven Part starts.

## Files touched (summary)

- `src/relay/racecast-feeds.py` — `ProducerSource`, `PartStore`, relay stream-target apply,
  `/parts/*` endpoints, wiring in `main()`, `--producer-tab` flag.
- `src/scripts/parts.py` *(new, pure)* — `parts_intent_phrase`, the `/parts/data`
  view-model builder (keeps the relay thin; mirrors `cue_admin.py` / `producer.py`).
- `src/scripts/console_policy.py` — `parts` requirement.
- `src/scripts/event.py` — launch-env resolver (DISPLAY/XAUTHORITY).
- `src/racecast.py` — `event start` Part reset + `--part N`; pass `--producer-tab`/overlay
  args as needed.
- `src/director/director-panel.html` — Part-aware control + typed-confirm modal + fallback.
- `tools/cloud/provision.py` — xfce autostart entries for OBS + Discord.
- Tests: `tests/test_parts.py` *(new)*, plus extensions to `test_console.py`,
  `test_event.py`.
- Docs: `director-panel.png`, `Run-an-event.md`, `tools/cloud/README.md`, `Sheet-Webhook.md`.

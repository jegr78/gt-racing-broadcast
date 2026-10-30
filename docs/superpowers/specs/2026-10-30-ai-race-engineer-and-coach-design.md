# AI Race Engineer and AI Coach as separate Solo POV features

Date: 2026-10-30. Amends, does not replace, the delivered designs of epic #969
(Race Engineer driver radio, modelled on the "GT Race Engineer" app) and epic
#977 (AI driving coach, modelled on Coach Dave Delta and similar sim-racing
coaches). Everything those epics settled stays valid unless this document says
otherwise: deterministic facts first, optional configured AI explanation, English
text and speech, text before audio, explicit training start, calibration rules,
message bounds and expiry, one native playback slot.

## Why

First real use on a Solo POV profile (2026-10-30) showed:

- The two products are not distinguishable. Engineer answers, proactive
  warnings and coaching share one "Driver radio" block, one setup form and one
  message lane. The producer could not tell where engineer or coach hints appear.
- Setup blocked on the per-driver identity grant ("Driver identities"), which a
  Solo POV profile does not need: the producer is the only driver.
- There was no way to show the hints in the broadcast picture.

Two defects seen in the same session are fixed separately and are not part of
this design: the coach discarded its evidence on every lap because the automatic
provisional sector proposal changed per lap (PR #1019), and the Control Center
coaching buttons flicker on every coach revision (own fix).

## Decisions (the user's)

1. **Three distinct things.** The human Race Engineer stays exactly as it is
   (Crew role plus the read-only `/console/race-engineer` desk). In addition there
   are two AI features, named **AI Race Engineer** and **AI Coach**. Neither is a
   Crew role.
2. **Solo POV only.** Only a Solo POV profile has AI features. There the producer
   is the only driver.
3. **No per-driver grant.** The "Driver identities" list and its check go away for
   both AI features. Anyone signed in to the profile's Console may open the
   driver view and use its controls. The Console sign-in itself stays (the
   `/console` prefix is reachable over Tailscale Funnel; nothing under it becomes
   unauthenticated).
4. **Coaching starts deliberately** (Start training), as #977 already specifies.
5. **Three outputs for both AI features:** audio on the relay PC (native voice,
   exists), text on a second device (the driver view in Console, exists), and new
   HUD elements in OBS.
6. **HUD elements.** Two new HUD slots, **"Race engineer"** and **"Coach"**, built
   like the existing Race Control slot (`#race-control` in `src/obs/hud.html`):
   - each is its own `data-edit` text slot in the overlay builder, freely
     positionable and styleable like other text slots;
   - it appears when its feature publishes a new message and disappears when that
     message is removed or expires (empty means hidden, the `.empty` pattern);
   - the builder's general **Visible** switch decides per slot whether messages are
     shown at all, so engineer and coach are switchable independently.

## Target behaviour

### AI Race Engineer (#969 scope)

Preset answers (fuel, pace, four tyre temperatures, short status), proactive fuel
warnings and completed-lap pace notices, optional AI explanation. Enabled per
Solo POV profile with one switch; while enabled it runs whenever live telemetry
is present. No training, no start button.

### AI Coach (#977 scope)

Explicit Start/Stop training, objective, calibration, one focus, progress, debrief
and private history exactly as #977 defines. Enabled per Solo POV profile with its
own switch; training still needs Start.

### Separation

The user decided that the two features are separate (decision 1). The split
below is the author's proposal for how, marked so it can be corrected.

- **Settings (author's call):** the Control Center Telemetry view shows two cards, "AI Race
  Engineer" and "AI Coach", each with its own enable switch and its own optional
  AI agent choice. Shared native-voice settings (microphone, wake phrase,
  transcription, Google speech, input language) sit in a third card "Voice", used
  by both.
- **Driver view (Console) and Director Panel Solo card:** two clearly labelled
  sections, "AI Race Engineer" (presets, latest answer, history) and "AI Coach"
  (training controls, focus, debrief). Each message is attributed to its
  feature.
- **Messages:** every message carries its source (`engineer` or `coach`). The HUD
  data exposes the current engineer message and the current coach message
  separately, each only while valid.
- **Audio:** both features share the existing single playback slot and its
  urgency rules.

### One driver per profile (user decision)

There is exactly **one training and one coach history per Solo POV profile**, no
matter where it was started: Control Center, Director Panel, native voice or any
Console sign-in. The per-owner model of #977 (`local:producer` versus
`console:<subject>`, "busy, another owner") is replaced by a single profile-level
driver. Existing per-owner histories of the profile are merged once into the
profile history (newest first, the existing 32 entries / 90 days bounds apply).
Any view of the profile sees and controls the same training.

### Access consequence (user decision, recorded)

"Anyone signed in to the profile's Console" includes commentators, Race Control,
Race Engineer desk users and Discord-OAuth users. All of them may request presets
(spoken into the driver's ear, optionally starting configured AI calls), write
setup/service declarations, start or stop training and delete the coach history.
The user accepted this explicitly ("jeder, keine Einschränkung"). Sign-in is still
required for every route.

### What the HUD slots show (author's call, delegated by the user)

- **Race engineer slot:** the deterministic text of the current engineer message,
  for preset answers, voice-question answers and proactive fuel/pace notices
  alike. Never the spoken question, never an AI interpretation. It shows while
  the message is valid (30 s, urgent fuel 15 s, as in #969) and clears when it
  expires, is superseded or is invalidated (session/context/setup change, pause).
- **Coach slot:** the action line of the latest per-lap review together with the
  current focus (one line, auto-fit). It stays until the next review replaces it,
  training stops, or the training context is invalidated. Calibration progress
  and the debrief are not shown in the HUD. Source is the coach's current state,
  not its short-lived radio message, so the slot does not flash for 12 s only.
- **Defaults:** both new slots start **hidden** in the overlay builder, so nothing
  reaches a live stream until the producer switches Visible on.

### Voice (author's call)

One shared native-voice configuration ("Voice" card). Both features speak through
it when voice is enabled; there is no per-feature speech switch in this version.
Voice requests for a disabled feature are refused with a spoken/text hint.

### Unchanged

The human Race Engineer desk, its role, access and pin flow. The telemetry
workspace. The Race Control slot. Non-Solo profiles.

## Goals

- A producer can tell at a glance which feature said what, and where.
- Enabling either AI feature on a Solo POV profile is one switch, with no identity
  list.
- Engineer and coach hints can be placed in the broadcast picture independently.

## Non-goals

- AI features for non-Solo profiles or more than one driver.
- Automatic coaching without Start.
- New spoken languages, new AI providers, browser audio.

## Anti-goals

- Any unauthenticated route under `/console` or any new Funnel mount.
- HUD state kept only in page JavaScript (it must survive an OBS browser-source
  reload, so it is served by the relay).
- A message staying in the HUD after it expired or was invalidated.

## Constraints

- Repository rules in `CLAUDE.md` (English code and docs, relay security
  boundaries, wiki screenshot refresh in the same change, TDD).
- Existing message bounds and expiry from `src/docs/design/driver-radio-service.md`.
- Overlay builder slot rules from `src/ui/CLAUDE.md` (`data-edit` markers in
  `hud.html` are the single slot source; text auto-fit applies to the new slots).

## Left to the builder

- Storage migration from the current profile radio settings (one `enabled` flag
  plus `drivers`) to two feature switches; the old `drivers` list is ignored. A
  profile that had radio enabled starts with both features enabled.
- The history merge mechanics and the replacement of the owner-matrix tests.
- Exact HUD data field names and default slot geometry.
- Message source attribution in history, routes and the native queue.
- How the Director Panel Solo card fits both sections.

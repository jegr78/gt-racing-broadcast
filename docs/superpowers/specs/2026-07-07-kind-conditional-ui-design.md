# Kind-conditional UI (Director Panel + Control Center)

Epic: #300 (Solo mode). Issue: **#307**. Builds on the profile `kind` field
(#301), the feed-less solo relay mode (#302), the solo OBS templates (#303) and
the device enumeration in General Settings (#304). This is the UI layer: adapt
the two operator surfaces to the active profile's `kind` so a **solo** profile
shows only what's relevant and never renders (or crashes on) the endurance
A/B-feed affordances.

## Context

Solo is the reframed *"endurance minus the A/B-feed schedule"* — the Sheet stays
always on, only the tabs that drive the A/B feeds (`Schedule`/`Qualifying`) are
irrelevant. The relay already runs feed-less in solo (#302) and reports its mode
via `/status`. The two surfaces have **not** been touched yet:

- **Director Panel** (`src/director/director-panel.html`) is served as a static
  page by the relay (`/panel`) and polls `/status`. It is currently **not
  solo-safe**: the status poll reads `d.feeds.A.stint` / `d.feeds.B.stint`
  unconditionally (director-panel.html:1414-1416), but solo's `_solo_status`
  returns `feeds: {}` (racecast-feeds.py:5399), so a solo relay would throw in
  the panel. #307 must fix this regardless of the cosmetic cut.
- **Control Center** (`src/ui/control-center.html` + `src/ui/ui_server.py` +
  `src/racecast.py`) knows **nothing** about a profile's `kind` today. Its
  new-profile dialog is endurance-only (Name + Copy-from), even though
  `pa.create_profile` and the CLI `profile new` already accept
  `--kind`/`--template` (#301).

## Key decisions

1. **Signal via the existing `mode`/`solo` status, not a new field.** The relay's
   `/status` already carries `mode:"solo"` / `solo:true` for a solo relay
   (racecast-feeds.py:5399) and the panel already gates race-vs-qualifying off
   `status.mode` via `applyMode()` (director-panel.html:1431, 2037-2043). #307
   extends that exact precedent with an `applySolo(d.solo)` toggle — no new relay
   field, no new endpoint on the panel side.
2. **Control Center learns `kind` from `/api/profiles`, not the relay.** The CC
   must gate views even when the relay is not running, so `kind` is read from the
   profile config (`resolve_config`/profile.env), not from the live relay status.
   `profiles_data()` gains a per-profile `kind` (and the active profile's kind is
   therefore available). This is static profile config — always available.
3. **Client-side `.hidden`/class toggling — the repo's dominant pattern.** Both
   surfaces are vanilla JS with no framework. The whole cut is expressed by
   toggling a `body.solo` class (panel) / gating a view (CC) after the data poll,
   matching the four existing precedents (`applyMode`, 404-self-hide,
   `?probe=1`-reveal, `os`-driven action hiding). No server-rendered variants.
4. **Endurance path stays byte-identical.** Every solo branch is additive and
   guarded on the solo signal; with `solo` false/absent the DOM, CSS, and data
   flow are unchanged. No existing test is disabled.

## Design

### A. The solo signal

**Director Panel.** In the `/status` poll handler:
- Read `d.solo === true` (equivalently `d.mode === "solo"`).
- Call a new `applySolo(isSolo)` that sets/clears `document.body.classList`
  `solo`. A `body.solo { ... }` CSS block hides the endurance-only containers
  (list in section B). This mirrors `applyMode()`.
- Make the existing feed reads solo-safe: guard `d.feeds && d.feeds.A` before
  dereferencing `.stint`/quality, so the poll never throws under `feeds: {}`.
  In solo the feed pills/tiles are hidden anyway, so the guarded values are just
  skipped.

**Control Center.** `profiles_data()` (racecast.py:4178-4198) adds `kind` to each
profile entry (resolved via the same profile-config read it already does for
`sheet_set`/`display`). The front-end resolves the **active** profile's kind from
that payload and toggles a `body.solo` class (or per-view gating — section C).

### B. Director Panel — the solo cut

Hidden when `body.solo` (endurance shows all of these unchanged):

| Container | id / selector | Why |
|---|---|---|
| Feeds section | `#feedsBus` (+ its `.bus` wrapper) | A/B feed health — no feeds in solo |
| Header feed pills | `#stA`, `#stB` | Feed A/B status pills |
| Preview feed tiles | `.pvtile[data-feed="A"]`, `.pvtile[data-feed="B"]` | A/B preview — keep program + POV tiles |
| Schedule editor (race) | `#raceSched` | A/B stint schedule |
| Schedule editor (qual) | `#qualSched` | qualifying schedule |
| Mode chip / switch | `#modeChip`, `#modeSwitch` | race⇄qualifying is meaningless in solo |
| Pending submissions | `#subsBox` | commentator stint-link submissions — no stints |
| Substitution | `#subSec` | stint substitution |

Kept and functional in solo (all feed-independent, already work under the solo
relay): Live Preview (program tile + **POV tile**), **Parts control**
`#partControl`/`#partActionBtn` (the chosen stream start/stop — a single
Producer "Part 1" row drives OBS stream key + start/stop + the post-event
report; `producer.active_producer_rows(rows, "solo")` already returns the
race/numeric parts), Cues `#cuesBus`, HUD/Setup value inputs
(`#setupRow`/`#teamRow`/`#condRow`/`#setupInfo` + the SETUP tab
Scn·Vis/Gfx/Flag-Gfx), **Timer** `#timerBus`, Audio `#audio`, Transition
`#txBar`, **OBS control** `#obsBus`, the chat rail, broadcast-chat, and the
program-audio bar.

**POV structural lift.** The POV editor (`#povName`/`#povUrl`/`#povSave`) lives
*inside* the `#urlsBox` "Schedule" `<details>`. Solo hides the schedule but keeps
POV, so POV is **lifted into its own top-level `.bus` card** that is visible in
both modes; in endurance it renders identically to today (same inputs, same
handlers, same POST to `/pov/set`) — only its DOM position moves out of
`#urlsBox`. This keeps a single POV code path and avoids POV being collateral of
the hidden schedule box. (Verify the move is byte-equivalent behaviorally: the
POV handlers bind by id, not by DOM ancestry.)

The header pills `#stA`/`#stB` and the transition/OBS/timer logic are otherwise
untouched.

### C. Control Center — creation dialog + view gating

**New-profile dialog** (`control-center.html:691-700`, `newProfile()` at
2882-2891, route `ui_server.py:704-715`, backing `profile_new_data`
racecast.py:4217):
- Add a **Kind** `<select>` (endurance | solo). When `solo` is selected, reveal a
  **Template** `<select>` (commentary | pov) and **disable the Copy-from**
  control (the CLI forbids `--from` with `--kind solo`,
  profile_admin.py:129-131) — mirror that rule client-side so the UI can't
  submit an invalid combo.
- `newProfile()` sends `{name, from, kind, template}`.
- `/api/profile/new` passes `kind`/`template` through; `profile_new_data(name,
  source, create, kind=..., template=...)` forwards them to
  `pa.create_profile` (already parameterized). Endurance default (`kind` absent
  ⇒ `endurance`, `template` empty) reproduces today's behavior exactly.

**View gating for an active solo profile:**
- Hide the **Streams** view (`data-view="streams"`, the feed/schedule surface)
  — both its nav entry and pane.
- Hide the feed-status block on the **Home** dashboard (the relay/feed rows that
  assume A/B).
- Make the **Solo devices** picker (added in #304, currently always in General
  Settings) **solo-only** — shown for a solo profile, hidden for endurance.
  *Superseded by #720:* the section is now **Devices** and shows for every kind. The
  capture card and mic rows serve an endurance `local:` stint (#592, #670); only the
  webcam, tyres/fuel and GT7 PlayStation rows stay solo-only (`.solo-only`).
- Gating reads the active profile's `kind` from `/api/profiles`; a `body.solo`
  class (or equivalent per-nav `data-kind` gate) drives the CSS, matching the
  existing `showView`/nav pattern. Switching the active profile re-reads
  `/api/profiles` and re-applies.

### D. Interfaces touched (summary)

- `src/relay/racecast-feeds.py`: **no change** — the solo signal already exists
  on `/status`. (The panel is a static file; the relay serves it unchanged.)
- `src/director/director-panel.html`: `applySolo()`, `body.solo` CSS, solo-safe
  feed guards, POV lifted to its own card.
- `src/racecast.py`: `profiles_data()` adds `kind`; `profile_new_data` gains
  `kind`/`template` params forwarded to `create_profile`.
- `src/ui/ui_server.py`: `/api/profile/new` forwards `kind`/`template`.
- `src/ui/control-center.html`: kind/template in the new-profile dialog; view
  gating; solo-only devices picker.

## Testing (additive — nothing disabled)

- `tests/test_racecast.py`: `profile_new_data` forwards `kind`/`template` to a
  `create` seam (assert the call receives them; endurance default path
  unchanged). `profiles_data` includes `kind` per profile.
- `tests/test_ui_server.py`: `POST /api/profile/new` with a solo body reaches
  the backing function with `kind="solo"`/`template=...`; `GET /api/profiles`
  shape carries `kind`.
- Panel JS is not unit-tested in this repo; its correctness is covered by the
  visual verification + the e2e harness surface (the panel already loads under a
  solo relay in a dev build). The solo-safe feed guard is verified by loading the
  panel against a solo relay (no console throw).
- No real device ids / IPs / machine paths enter git; endurance path
  byte-identical.

### Visual verification + wiki screenshots (CLAUDE.md hard rule)

Any change to the Director Panel and Control Center requires the
`ui-visual-verification` look **and** refreshed committed wiki images in the same
change:
- `src/docs/wiki/images/director-panel.png` — the panel. A solo-variant capture
  demonstrates the cut (solo demo profile / solo relay stand-in). Decide during
  implementation whether to replace the canonical shot or add a solo companion
  image; at minimum the existing `director-panel.png` must still be accurate for
  endurance and must not regress.
- Affected `cc-*.png` — the new-profile dialog (with the kind/template selector)
  and, if a distinct solo dashboard state is shown, its capture. Captured from a
  local dev build (no `VERSION`), per the uniform dev-build-badge rule.

## Non-goals / boundaries

- No new relay endpoint or new `/status` field — the solo signal already exists.
- No change to the endurance panel/CC behavior or layout (byte-identical).
- Rebrand to "GT Racing Broadcast" — **#308**. GT7 telemetry POV HUD — **#324**.
- No server-rendered UI variants; the cut is client-side, matching the repo.

## Success criteria

- Loading the Director Panel against a **solo** relay: no console error, the
  feed/schedule/qualifying/submission affordances are hidden, and the Parts
  control, HUD/Setup inputs, timer, OBS control, and POV editor are present and
  functional. Against an **endurance** relay the panel is byte-identical to
  today.
- Control Center new-profile dialog can create a **solo** profile (kind +
  template, Copy-from disabled); with a solo profile active, the Streams view and
  the Home feed block are hidden and the Solo-devices picker is shown; with an
  endurance profile active, everything is exactly as today.
- `director-panel.png` + affected `cc-*.png` refreshed and committed; full suite
  green, no test disabled; endurance path unaffected.

---

## Increment 2 — full solo-capable Director Panel (OBS control) + commentary mic

Increment 1 (above) hides the endurance *feed/schedule* affordances and reveals
POV. During visual verification a gap surfaced: the panel's **OBS control** —
Scn·Vis scene switches, source-visibility toggles, Gfx toggles, the scene macros,
and the Audio faders — is built from a **hardcoded endurance `CONFIG`**
(`director-panel.html:801`) that references scenes/sources/inputs
(`Stint`, `Splitscreen`, `Feed A`, `Feed B`) that **do not exist** in the #303
solo OBS collection (`GT Racing Solo`: `Program`, `Solo Capture`, `Solo Webcam`,
…). So a solo operator's OBS buttons target missing scenes. The header
`ON AIR STINT · A` pill (`#stAir`) and the feed actions (`NEXT`/`RELOAD A/B`) are
likewise endurance-only.

A second gap: the solo collection has **no commentary microphone** — only game
capture, webcam, Discord, POV, and intermission music. A solo commentary/POV
broadcast needs the commentator's **own mic**, captured on the producer machine.

Decisions (agreed with the user): make the panel OBS control **kind-aware** and
**add the commentary mic across the stack**, all within #307.

### A. Solo OBS control — `CONFIG_SOLO` + rebuild-on-solo (`director-panel.html`)

The panel builds its Scn·Vis/Gfx/Audio buttons **once at page load** from the
top-level `CONFIG`, before the first `/status`. To adapt, the button-building
becomes a function `buildControls(cfg)`; the panel builds the endurance set by
default and, when the **first** `/status` reports `solo`, clears and **rebuilds**
the Scn·Vis (`#scnBus`), Gfx (`#gfxBus`), and Audio (`#audio`) buses from
`CONFIG_SOLO` (once — guarded by a `builtSolo` latch). Endurance is byte-identical
(the default build path is unchanged; the rebuild only runs under `solo`).

`CONFIG_SOLO` (derived 1:1 from the `GT Racing Solo` collection):
- **macros:** none (solo has no feed-swap workflow; the operator uses the Audio
  faders — this avoids inventing on-air mute conventions).
- **scenes** (plain scene switches): `Program`, `Interview`, `Standby`,
  `Intermission`, `Intro`, `Outro`, `Discord`.
- **vis** (PiP visibility toggles, all in `Program`): `WEBCAM` → `Solo Webcam`;
  `POV` → `Feed POV` (via the existing `/pov/toggle`, `relay:"pov"`).
- **graphics** (all in `Program`, except POST-RACE in `Interview`): `HUD`
  (`Stint HUD`), `STANDINGS`, `SCHEDULE`, `RACE RESULTS`, `QUALI RESULTS`,
  `RACE WX 1`, `RACE WX 2`, `QUALI WX`, `STBY COVER`, `POST-RACE`.
- **audio faders:** `Game` (`Solo Capture Device`), `Webcam`
  (`Solo Webcam Device`), `Mic` (`Commentary Mic Device` — added in B),
  `POV` (`Feed POV`), `Discord` (`Discord Audio Capture`), `Intermission`
  (`Intermission Music`).

Other kind-conditional bits:
- **Feed actions:** in solo, keep only `POV RELOAD` / `POV STOP`; hide `NEXT`,
  `RELOAD ALL/A/B`, and the `FEEDS → STINT…` correction (add these to the
  `body.solo` hide set / gate their build under `!solo`).
- **Header `#stAir`:** hide in solo (no stints) — add to the `body.solo` CSS
  hide set.

### B. Commentary mic (collection + device localization + enumeration + panel)

A new **`Commentary Mic Device`** audio input, wrapped in a nested
**`Commentary Mic`** scene (mirroring the Discord audio-scene precedent —
`_device_scene` clones the `Discord` scene), tokenized **`__RACECAST_MIC__`**.
Routing: the `Commentary Mic` scene is included as an item in the on-air scenes
**`Program`, `Interview`, `Standby`, `Intermission`, `Discord`** — audible in all
of them, **muted during `Intro`/`Outro`** (the rendered clips have their own
audio). One leaf + one wrapper scene + five references (same model as `Discord`).

- **Collection (`tools/derive-solo-templates.py` + both `src/obs/GT_Solo_*.json`):**
  add fixed synthetic UUIDs for the mic leaf + scene; build the
  `Commentary Mic Device` leaf (committed default kind `coreaudio_input_capture`,
  settings `{"device_id": "__RACECAST_MIC__"}`); clone the Discord scene into a
  `Commentary Mic` scene wrapping the leaf; add that scene as an item to the five
  target scenes; prepend/append `Commentary Mic` in `SCENE_ORDER`. Regenerate both
  `GT_Solo_Commentary.json` and `GT_Solo_POV.json` deterministically (the #303
  regeneration cross-check still holds).
- **Localization (`src/setup-assets.py`):** tag each `DEVICE_SOURCES` entry with a
  `kind` (`"video"`/`"audio"`); add `AUDIO_VARIANTS`
  = `{darwin: ("coreaudio_input_capture","device_id"), win:
  ("wasapi_input_capture","device_id"), linux:
  ("pulse_input_capture","device_id")}`; `localize_device_sources` picks the audio
  vs video variant per entry and injects `RACECAST_MIC` for the mic. Same
  best-effort contract (empty value ⇒ WARNING; unknown platform ⇒ unset).
- **Device scan (`src/scripts/obs_ws.py` + `src/racecast.py` + CC):** audio inputs
  enumerate via the **`device_id`** property on all OSes
  (`device_property_name` returns `"device_id"` for the audio case — cross-checked
  against `AUDIO_VARIANTS` like the video map is against `DEVICE_VARIANTS`).
  `racecast device-scan` gains a **mic** enumeration/selection (flag `--mic`) →
  writes `RACECAST_MIC` (via the existing `env_upsert_data`). The Control Center
  **Solo devices** section gains a third **Mic** dropdown (`/api/devices` returns a
  mic list; `/api/devices/select` accepts a `mic` key). `.env.example` documents
  `RACECAST_MIC`.
- **Panel:** the `Mic` fader in `CONFIG_SOLO.audio` (input `Commentary Mic Device`)
  — done in A.

### Testing (additive)

- `tests/test_setup.py` (or the device-localization test): the mic uses the audio
  variant per OS (coreaudio/wasapi/pulse `_input_capture`, `device_id`); a
  cross-check that `AUDIO_VARIANTS` agrees with `obs_ws`'s audio property name.
- `tests/test_obsws.py`: audio `device_property_name` = `device_id`.
- `tests/test_racecast.py`: `device-scan` `--mic` writes `RACECAST_MIC`;
  `env_upsert_data` preserves the other device keys.
- `tests/test_ui_server.py`: `/api/devices` carries a mic list; the select route
  accepts `mic`.
- A regeneration check that both `GT_Solo_*.json` are byte-identical to a fresh
  `derive-solo-templates.py` run (deterministic), and contain the `Commentary Mic`
  scene in the five target scenes and NOT in Intro/Outro.
- Panel OBS control is verified by the controller against a live solo relay
  (Playwright): the Scn·Vis/Gfx/Audio buses show the solo button set (Program /
  Webcam / POV / Game / Webcam / Mic …), not endurance scenes; `#stAir` hidden.

### Visual verification + screenshots

- The **solo** Director Panel (final, with the solo OBS control) gets a committed
  companion image `src/docs/wiki/images/director-panel-solo.png`; the endurance
  `director-panel.png` stays byte-identical (unchanged). Re-verify + re-record the
  visual marker for `director-panel.html`.
- The Control Center **Solo devices** section now shows three dropdowns
  (Webcam / Capture / Mic) — refresh the relevant `cc-*.png` if the mic dropdown
  is in frame (it sits below the fold in `cc-settings.png`; refresh only if the
  visible framing changes).

### Increment-2 boundaries

- The mic model is scene-bound (in the five scenes), not an OBS *global* audio
  device — per the user's routing choice.
- No change to the endurance `CONFIG` or endurance collection — endurance stays
  byte-identical; `CONFIG_SOLO` and the mic live only in the solo paths/collection.

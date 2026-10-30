# AI Race Engineer and AI Coach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Solo POV driver radio into two separately switchable features, AI Race Engineer and AI Coach, without per-driver grants, with one training per profile and two HUD slots, and fix the coaching button flicker.

**Architecture:** The relay keeps one radio service, one coach service and one native playback slot. The profile radio settings gain two feature switches and lose the driver list. Every message carries its `source` (`engineer` or `coach`), every coach call uses one profile-level owner, and `/hud/data` exposes the current engineer text and coach line. The browser component renders two labelled sections and the Control Center three cards. The HUD gets two `data-edit` text slots that are hidden by default.

**Tech Stack:** Python 3 stdlib (relay, scripts), vanilla JS in `src/console/driver-radio.js`, HTML/CSS in `src/obs/hud.html`, overlay compiler `src/scripts/overlay_build.py`. Tests are stdlib scripts under `tests/`; JS checks run under node.

**Spec:** `docs/superpowers/specs/2026-10-30-ai-race-engineer-and-coach-design.md`. It amends `src/docs/design/driver-radio-*.md` and `driver-coach*.md`; read both.

## Global Constraints

- English code, docs, UI text and spoken text. ASCII argparse help.
- Every route under `/console` stays authenticated (Console token or Discord). No new route outside it, no Funnel change.
- AI features exist only for Solo POV profiles (`radio_access.available`). Other profiles are unchanged.
- No per-driver grant: any verified Console subject of the profile may use both features.
- One training and one coach history per profile. The owner is `{"profile": <profile>, "subject": "local:producer"}` for every caller.
- Message bounds and expiry are unchanged: history 32, queue 4, routine 30 s, urgent fuel 15 s, coach radio message ≤ 12 s.
- New HUD slots are hidden until the producer turns Visible on, and an empty slot is never shown.
- Changed UI surfaces refresh their wiki screenshots in the same PR (`src/ui/CLAUDE.md`).
- Gates per PR: `python3 tools/run-tests.py`, `python3 tools/lint.py`, `python3 tools/build.py`; relay changes also `python3 tests/test_pov.py`.

## Delivery

One parent issue "AI Race Engineer and AI Coach as separate Solo POV features" (links the spec) with one sub-issue and one PR per task, in this order. Task 1 is independent and ships first. Tasks 2 to 4 share the settings schema in that order. Tasks 5 and 6 depend on Task 4.

## Review Focus

1. A profile saved before this change (`enabled: true`, `drivers: [...]`, no `features`) still loads, and both features come up enabled. Covered by `t_legacy_settings_migrate_to_both_features` (Task 2).
2. A preset request or voice question while the AI Race Engineer is off is refused with `feature_disabled` and never reaches the playback queue. The coach still works. Covered by `t_engineer_off_refuses_presets_but_coach_runs` (Task 4).
3. Training started from the Control Center shows as the same running training on a phone Console view, which can stop it. Covered by `t_one_training_across_local_and_console` (Task 3).
4. With Visible on and no current message, both HUD slots stay hidden, including after an OBS browser-source reload. Covered by `t_hud_slots_hidden_when_empty_even_if_visible` (Task 6).
5. A transient 503 during a coach poll does not disable buttons that were enabled. Covered by `t_coach_buttons_survive_a_transient_failure` (Task 1).

---

### Task 1: Coaching controls stop flickering

The `CoachView.fail()` branch for a missing code or status ≥ 500 (`src/console/driver-radio.js`, the `if(!error.code||error.status>=500)` block) replaces `this.state` with a `phase:'unavailable'` stub and re-renders. Every button gets disabled until the next good poll re-enables it. That is the flicker.

**Files:**
- Modify: `src/console/driver-radio.js` (`CoachView.fail`)
- Test: `tests/test_driver_radio_ui.py` (node-run JS check, same harness style as the existing component checks)

**Interfaces:** none new.

- [ ] **Step 1: Write the failing test** `t_coach_buttons_survive_a_transient_failure`. Render a CoachView with an active training (`active:true, pending:false`, a focus), record every button's `disabled`, call `fail({status:503, code:'coaching_unavailable'})`, render again and assert:

```python
assert before == after, "a transient failure keeps the last controls"
assert error_text == "Coaching is unavailable."   # or the relay message when given
```

Also assert that `fail({status:403, code:'forbidden'})` still clears the view.

- [ ] **Step 2: Run it.** `python3 -c "import sys; sys.path.insert(0,'tests'); import test_driver_radio_ui as t; t.t_coach_buttons_survive_a_transient_failure()"` should FAIL on the `before == after` assertion.
- [ ] **Step 3: Implement.** In `fail()`, a 5xx or code-less error only sets the error text and keeps `this.state` and the rendered controls. A transient failure must not set `phase:'unavailable'`. Auth and identity codes keep calling `clear()`.
- [ ] **Step 4: Run the test** (PASS) and `python3 tests/test_driver_radio_ui.py`.
- [ ] **Step 5: Look at it.** Use the `ui-visual-verification` skill on the Control Center Telemetry view, then run `python3 .claude/hooks/record_ui_verified.py src/console/driver-radio.js`.
- [ ] **Step 6: Commit** `fix(radio): keep coaching controls through a transient poll failure`.

### Task 2: Two feature switches, no driver grant

**Files:**
- Modify: `src/scripts/driver_radio_access.py` (`_valid`, `Store.read`, `Store.update`, `Store.authorize`; remove `_driver_identities` from PR #1021 if merged)
- Modify: `src/relay/racecast-feeds.py`: every `radio_access.authorize(subject)` call (`_coach`, `_radio`, the radio-allowed checks near the `/console` gate) and the `_coach` admin `config['enabled']` check
- Test: `tests/test_driver_radio_access.py`, `tests/test_driver_radio_ui.py`, `tests/test_driver_coach_http.py`

**Interfaces:**
- Produces settings document version 2:
  `{"version": 2, "revision": int, "features": {"engineer": {"enabled": bool, "agent": str|None}, "coach": {"enabled": bool, "agent": str|None}}, "voice": {...unchanged}, "limits": {...}, "providers": {"speech": bool}, "native": {...}}`. No `enabled` and no `drivers` key. `agent` moves from `providers` into each feature.
- Produces `Store.authorize(feature: str) -> dict` with `feature` in `{"engineer", "coach"}`. It raises `RadioError('source_unavailable')` for a non-Solo profile and `RadioError('feature_disabled', 'Enable the AI Race Engineer for this profile' | 'Enable the AI Coach for this profile')` when off. On success it returns `{"version": 2, "enabled": True, "revision": int, "feature": feature}`. It takes no subject: the caller has already verified the Console token.
- Produces `Store.enabled(feature: str) -> bool` (never raises for a readable document).

- [ ] **Step 1: Write failing tests.**
  - `t_legacy_settings_migrate_to_both_features`: a v1 file `{"version":1,"revision":4,"enabled":true,"drivers":["x"],"providers":{"speech":true,"agent":"a1"},...}` reads as v2 with both features enabled, `agent:"a1"` on both, `speech:true` and revision 4. A v1 file with `enabled:false` reads with both disabled.
  - `t_features_switch_independently`: an update with `{"expected":0,"features":{"engineer":{"enabled":true,"agent":null},"coach":{"enabled":false,"agent":null}}}` makes `authorize('engineer')` succeed and `authorize('coach')` raise `feature_disabled` with the coach message.
  - Replace `t_access_is_explicit_profile_scoped_and_revocable` and `t_driver_identities_are_saved_as_console_writes_them`: there is no per-subject check, and a `drivers` key in an update is `invalid_settings`.
  - HTTP (`test_driver_radio_ui.py`): a Console subject that was never assigned reaches `/console/driver-radio/snapshot` (200) once the engineer is enabled. It gets 403 `feature_disabled` when the engineer is off. Without a token it gets 401.
- [ ] **Step 2: Run them** and see each FAIL on its assertion.
- [ ] **Step 3: Implement.** The v1→v2 migration happens on read and is written on the next update. Per-feature agents are validated with the same regex as `_provider_settings` today. Relay call sites use `authorize('engineer')` for presets, snapshot, messages, setup, service, native and voice, and `authorize('coach')` for `_coach`.
- [ ] **Step 4: Run** the three test files, `python3 tests/test_pov.py` and lint.
- [ ] **Step 5: Docs.** Update `src/docs/design/driver-radio-access.md` (v2 schema, no grant) and wiki `Driver-Radio.md` "Enable access": both switches, no identities, sign-in still required.
- [ ] **Step 6: Commit** `feat(radio): switch AI Race Engineer and AI Coach separately, without driver grants`.

### Task 3: One training and one history per profile

**Files:**
- Modify: `src/relay/racecast-feeds.py` `_coach`: the owner is always `dict(profile=profile, subject='local:producer')`
- Modify: `src/scripts/driver_radio_service.py` `history()`: coach messages are visible to every requester. The `_coach_owner` subject filter goes; `_coach_owner` stays for attachments.
- Modify: `src/scripts/driver_coach_history.py`: add `merge_profile_histories(profile_root: Path) -> int`
- Test: `tests/test_driver_coach_http.py`, `tests/test_driver_coach_history.py`, `tests/test_driver_radio_service.py`

**Interfaces:**
- Produces `PROFILE_OWNER_SUBJECT = 'local:producer'` in `driver_coach_service.py`. The relay imports it.
- Produces `driver_coach_history.merge_profile_histories(profile_root) -> int`. It folds every other owner file of the profile into the `local:producer` file. Entries are ordered newest first by `ended_at`, then `started_at`, deduplicated by `id`, and cut to the existing 32 / 90-day bounds. The merged files are removed. It returns the number of entries moved and is idempotent. It runs once when the relay starts the coach for the profile.

- [ ] **Step 1: Write failing tests.**
  - `t_one_training_across_local_and_console`: start training via the admin route, then a Console subject's `GET /console/driver-radio/coach` shows `active:true` with the same `epoch`, and its `POST stop` succeeds. Never `training_busy`.
  - `t_coach_messages_reach_every_console_view`: `history(requester='alice')` includes a published coach message.
  - `t_merge_profile_histories_keeps_newest_32`: two owner files with 20 entries each merge into 32, newest first, the old file removed, and a second call returns 0.
- [ ] **Step 2: Run** and see them FAIL.
- [ ] **Step 3: Implement** as described. Owner-matrix tests that assert `training_busy` between local and Console owners are rewritten to the single-owner rule. Delete none silently: list them in the PR body.
- [ ] **Step 4: Run** the coach and radio test files, `test_pov.py` and lint.
- [ ] **Step 5: Docs.** Add a "One driver per profile" section to `src/docs/design/driver-coach-controls.md` and `driver-coach-history.md`. Update the wiki `Driver-Radio.md` history paragraph.
- [ ] **Step 6: Commit** `feat(coach): one training and one history per Solo POV profile`.

### Task 4: Messages carry their source and features gate their work

**Files:**
- Modify: `src/scripts/driver_radio_service.py`: `_publish` sets `source='coach' if kind == 'coach' else 'engineer'`. `refresh()` and `_announcements()` publish engineer work only while `settings.enabled('engineer')`. `publish_coach` returns `None` while the coach is off. `request()` and `question()` raise `RadioError('feature_disabled', ...)` while the engineer is off.
- Modify: `src/scripts/driver_radio_providers.py`: the AI explanation uses `features[source].agent`
- Modify: `src/scripts/driver_radio_native.py` (voice question path): a refused request speaks or shows the `feature_disabled` text
- Test: `tests/test_driver_radio_service.py`, `tests/test_driver_radio_providers.py`, `tests/test_driver_radio_native.py`

**Interfaces:**
- Produces a public message field `source: "engineer" | "coach"`.
- Produces `Service.hud_text(source: str, now: float | None = None) -> str | None`: the `text` of the newest message of that source whose state is not superseded, cancelled, expired or invalidated and whose `expires_at` is still ahead. Otherwise `None`.

- [ ] **Step 1: Write failing tests.**
  - `t_engineer_off_refuses_presets_but_coach_runs`: with the engineer off, `request('fuel', binding)` raises `feature_disabled`, no fuel alert is published and `publish_coach(...)` still returns a message with `source:'coach'`.
  - `t_hud_text_follows_message_life`: a routine engineer message gives its text through `hud_text('engineer')` until `expires_at`, then `None`. A newer engineer message replaces it. Invalidation gives `None`.
  - `t_explanation_uses_the_feature_agent`: a coach message requests the coach agent, an engineer message the engineer agent.
- [ ] **Step 2: Run** and see them FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the radio test files and lint.
- [ ] **Step 5: Commit** `feat(radio): attribute messages to AI Race Engineer or AI Coach and gate each`.

### Task 5: Two labelled sections and three settings cards

**Files:**
- Modify: `src/console/driver-radio.js`. The admin form becomes three cards: "AI Race Engineer" (enable plus agent), "AI Coach" (enable plus agent) and "Voice" (the existing voice, wake, transcription, speech and microphone fields). The driver view and Director Solo card show two sections, "AI Race Engineer" (presets, latest answer, history filtered to `source:'engineer'`) and "AI Coach" (the existing CoachView, coach messages). A disabled feature's section shows "Off · enable it in the Control Center".
- Modify: `src/racecast.py` `driver_radio_data`: the agent list stays, and the v2 settings pass through
- Modify: `src/ui/control-center.html` only if the mount container needs headings
- Test: `tests/test_driver_radio_ui.py`, `tests/test_engineer_program_still.py` (if it renders the Director card)
- Screenshots: `cc-telemetry.png`, `console-driver-radio-*.png`, `director-panel-solo.png`

**Interfaces:**
- Consumes the Task 2 settings v2, Task 3's single owner and the Task 4 `source` field.

- [ ] **Step 1: Write failing tests** (node). `t_admin_form_saves_two_features`: the submitted body has `features.engineer` and `features.coach` with their own `enabled` and `agent`, and no `drivers`. `t_sections_split_messages_by_source`: an engineer message renders only in the engineer section, a coach message only in the coach section.
- [ ] **Step 2: Run** and see them FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Then `ui-visual-verification` on the Control Center, the driver view and the Director Solo card. Refresh the three screenshot sets with the `wiki-screenshots` skill and record the UI marker.
- [ ] **Step 5: Docs.** Rewrite wiki `Driver-Radio.md` around the two features: where text appears, where audio appears, that the human Race Engineer desk is separate. Add a short note to `src/docs/design/driver-radio-ui.md`.
- [ ] **Step 6: Commit** `feat(radio): separate AI Race Engineer and AI Coach in the driver view and Control Center`.

### Task 6: HUD slots "Race engineer" and "Coach"

**Files:**
- Modify: `src/obs/hud.html`: add `<div id="race-engineer" class="el white" data-edit="Race engineer" data-edit-kind="text"></div>` and `<div id="coach" class="el white" data-edit="Coach" data-edit-kind="text"></div>` next to `#race-control`. Default geometry: `#race-engineer` at `left: 264px; top: 800px; width: 900px; height: 50px` and `#coach` at `left: 264px; top: 740px; width: 900px; height: 50px`, both `font-size: 28px`, styled like `#race-control`. Both are hidden by default: `display: none` unless the profile's Visible is on, and always hidden while `.empty`.
- Modify: `src/scripts/overlay_build.py`: slots in a new `DEFAULT_HIDDEN_SLOTS = ("race-engineer", "coach")` compile `visible: true` to `display: block` (as the intermission slots do), and the builder reads their default as not visible
- Modify: `src/relay/racecast-feeds.py`: `/hud/data` adds `"raceEngineer": radio_service.hud_text('engineer')` and `"coach": coach.hud_line()`, or `""` when there is no radio service (non-Solo profiles)
- Modify: `src/scripts/driver_coach_service.py`: add `hud_line() -> str | None`. It returns the action line of the latest review plus the current focus name, as `"<focus>: <action>"` in one line, while training is active and the context is valid. It returns `None` after Stop, invalidation or before the first review.
- Test: `tests/test_overlay.py`, `tests/test_hud*.py` (or whichever file covers `/hud/data`), `tests/test_driver_coach_service.py`
- Screenshots: `cc-overlay-builder.png` (and the HUD shot if one exists)

**Interfaces:**
- Consumes `Service.hud_text` (Task 4).
- Produces `/hud/data` keys `raceEngineer: str` and `coach: str`, empty when there is nothing to show.

- [ ] **Step 1: Write failing tests.**
  - `t_hud_data_carries_engineer_and_coach_text` (relay): with a current engineer message and a coach review, `/hud/data` has both strings. A non-Solo relay has both `""`.
  - `t_hud_slots_hidden_when_empty_even_if_visible` (node on `hud.html` + compiled CSS): with Visible on, an empty `raceEngineer` keeps `#race-engineer` hidden and a non-empty one shows it. With Visible unset, a non-empty value stays hidden.
  - `t_new_hud_slots_default_hidden` (`test_overlay.py`): `extract_slots` lists both, and a layout without `visible` compiles to no `display: block`.
  - `t_coach_hud_line_lives_with_the_review`: `hud_line()` is `None` before the first review, then the line, and `None` again after Stop.
- [ ] **Step 2: Run** and see them FAIL.
- [ ] **Step 3: Implement.** `hud.html`'s tick calls `setText("race-engineer", d.raceEngineer)` and `setText("coach", d.coach)`.
- [ ] **Step 4: Run** the tests, `test_pov.py`, lint and build. Look at the builder and a HUD with a sample message (`ui-visual-verification`). Refresh `cc-overlay-builder.png` and record the marker.
- [ ] **Step 5: Docs.** In wiki `Driver-Radio.md`, describe the HUD slots (what each shows, when it hides, Visible off by default). Add both slots to the overlay builder slot list in `src/ui/CLAUDE.md`.
- [ ] **Step 6: Commit** `feat(hud): show AI Race Engineer and AI Coach messages in their own HUD slots`.

# Scene layout builders

The Control Center edits each scene independently. The existing HUD layout remains
`layout-hud.json` and `hud.css`; Intermission uses `layout-intermission.json` and
`intermission.css`. Both live under the active profile's `overlay/` directory, so
profile bundles and look backups carry the complete design. Existing CSS migrates
verbatim into the layout's advanced CSS and retains cascade precedence.

Intermission is available in Endurance and both Solo templates. Endurance exposes
public chat; Solo additionally exposes the existing nested webcam and capture
scenes. The editor shows labeled video placeholders, never an unsolicited camera
preview. The background is the profile's existing Intermission graphic. Source
geometry is scoped to the Intermission scene, preserving Program and standalone
device scenes. Video items preserve aspect ratio and stay below the chat overlay.

Saving a layout writes design defaults. Applying it explicitly updates that scene's
mapped OBS transforms and visibility without cutting scenes or changing audio.
Automatic startup/refresh sync only hides items, preserving live operator hiding.
No video sources are added to Endurance Intermission. Legacy Solo POV webcam
placement and visibility remain intact until a layout is saved.

## Requirement and evidence matrix for #930

| Requirement | Production path | Positive test | Failure-path test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Profile-aware scene/slot selection | Overlay slots API and builder selector | Endurance chat; Solo chat/webcam/capture | Invalid page and absent profile return structured errors | Browser switches HUD/Intermission in both kinds |
| Independent layouts and CSS migration | Layout read/write and compiler | Saving Intermission leaves HUD files intact | Page mismatch and invalid layout rejected without writes | Reload restores edited chat geometry |
| Chat appearance and visibility | Intermission marked chat slot | Style/geometry/hidden CSS compilation | Empty legacy CSS preserves defaults | Rendered chat matches canvas geometry |
| Scoped video geometry | Overlay mappings, setup bake, OBS Apply | Intermission bounds match saved design | Program and standalone bounds unchanged | Fake OBS records only Intermission mutations |
| Visibility without camera surprises | Setup defaults, explicit Apply, automatic sync | Explicit apply shows/hides saved video items | Automatic refresh never re-enables operator-hidden items | Fake OBS captures startup versus explicit Apply |
| Source reuse and layering | Solo collection templates | Existing nested sources below chat | No duplicate video devices or Endurance video slots | Collection import inspection and offline preview |
| Background and offline editing | Background API, labeled canvas placeholders | Correct graphic per scene | Missing asset/OBS leaves usable editor and diagnostic | Browser preview with no OBS/devices |
| Portable design and compatibility | Profile bundle and backup workflows | Scene CSS/layout survive export and restore | Older collection missing items reports a useful status | Synthetic round trip; existing profile default screenshot |

Splitscreen will reuse the scene-selection contract in #935. Live controls and
profile-aware starter templates are tracked separately in #931.

## Verification record

`tests/test_scene_layouts.py` covers the data/API, OBS intent and bundle/backup
contracts. `tools/e2e-scene-layouts.py --shots` exercises the actual numeric input
handlers, saved geometry, browser reload, profile-aware slots and explicit Apply
against temporary profiles. It records four Intermission-only OBS mutations and
zero browser exceptions, without opening devices or using a real OBS instance.

Before final verification, the scene target, automatic hide-only rule, explicit
page allowlist and foreign-relay ownership guard were each deliberately broken
separately. Each mutation failed at its behavioral assertion; the committed
implementation was restored and the targeted suite passed again.

Self-review corrected a loader block accidentally inserted into numeric/shadow
handlers and added the foreign-profile Apply refusal. The independent read-only
security pass and its correction delta found no actionable security issues.

## Live controls and templates, #931 requirement matrix

| Requirement | Production path | Positive test | Failure-path test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Actual scene-specific visibility | IntermissionControl and /obs/intermission | Repeated chat/video toggles read OBS state; manual OBS change reflected | Missing individual item remains unknown; disconnected returns503 with error | Browser Director clicks and recording OBS fake |
| Profile gating | Controller key allowlist and builder template metadata | Solo gets three controls/four templates; Endurance chat/two templates | Endurance camera/capture actions400 with no mutation | Both profile browser scenarios |
| Defaults versus overrides | Runtime-only visibility store and explicit restore | Chat override survives new controller; restore applies saved CSS/video defaults | Write failure reported, malformed runtime state hides chat safely | Reload overlay, switch scenes, restart controller |
| No camera reveal on automatic refresh | Existing hide-only sync plus overlay frame polling | Hidden camera remains hidden after refresh/restart | Broken hide-only rule caught separately | Real handlers + fake OBS |
| Independent scene/audio/goals | Fixed scene/source map; templates only slot changes | Only Intermission item calls; no audio, cut or goal writes | Invalid key/verb rejects before OBS/file writes | Assert full OBS mutation trace and unchanged goals |
| Editable starter templates | Profile-aware slots metadata and builder select | Select previews, undo works, save writes only active scene | HUD/unsupported template is no-op; no OBS calls on selection/save | Actual select/input events with existing browser harness |
| Director authorization and Companion GET | Existing /obs director prefix and fixed path args | Director actions, loopback Companion GET dispatch | Other crew403; malformed request400 error shape | Live HTTP gate + real Companion button click |
| Operator documentation | Scene design, wiki, Director/Companion screenshots | Document precise lifetime and restore semantics | Older collections diagnostic documented | Capture real shipped pages in isolated local servers |

Implementation decisions:
- Chat show/hide uses an inline !important override in the browser, separate from profile CSS. Hiding chat must not disable the whole Intermission browser item because it also supplies video frames.
- A show may explicitly enable a manually hidden Intermission browser item. Toggle reads current effective state first; unknown state never guesses.
- Video source state always comes from OBS, including manual edits. A live action targets only Intermission, even if it reuses Solo Webcam/Solo Capture with Program.
- Runtime visibility JSON belongs below runtime/<profile>/ and is excluded from profile bundles and look snapshots. No auto reveal after restart. Restore is explicit and may reveal saved camera defaults.
- Source visibility is serialized across actions. Tests interleave two toggles and assert final OBS state as well as call trace.
- Break + goals hides video/chat and preserves any goal slots/configuration. Goal slots become available in #932; the template remains usable without them.
- Templates preserve unrelated slots/fonts/custom CSS/assets. They change the editor model only; Save and Apply stay explicit.
- The optional controller runtime path is injected by relay bootstrap; test handlers may use an in-memory controller. No tests connect to a real OBS or read machine runtime.
- Companion page 1 uses the four remaining fourth-row cells for Chat, Webcam, Capture and Defaults. Profile-aware export omits Solo video controls from Endurance; existing static buttons require re-export/import to change.

### Verification record for #931

Controller and live HTTP fixtures cover actual item reads, repeated and concurrent
toggles, absent sources, disconnected OBS, disk-write failure, corrupt runtime
state, restore, profile gating and refresh reconciliation. The shipped Director
and overlay pages run in `tools/e2e-intermission.py`; it also holds an older poll
response until after a newer action to verify that it cannot overwrite the UI.
The scene-builder browser scenario exercises actual template selection, Undo,
invalid/stale selection and profile-specific choices without OBS writes.

Nineteen guards were separately broken and observed red at their behavior
assertions, then restored: scene scope, source/profile allowlists, missing-source
refusal, action serialization, corrupt-state hiding, persistence before publish,
live override reconciliation, Companion/template profile filtering, Fetch Metadata,
origin matching and parsing, both HTTP method gates, stale/unsupported template
refusal, slot filtering and stale poll responses. A first negative test allowed
the Endurance guard to mask the source guard; Solo negative cases now isolate it.

Self-review corrected visibility-only video defaults and the first-save overlay
path. The independent security pass found foreign-browser control requests could
enable video. Both mutating routes now reject cross-origin browser metadata while
retaining headerless Companion access under the existing trusted-network model.
The correction pass found no remaining actionable security issue. HTTP fixtures
prove refusal leaves OBS and persisted overrides unchanged. Real buttons in an
isolated Companion instance were imported and clicked successfully. Changed wiki
screenshots and the reused slide image were captured from shipped pages.

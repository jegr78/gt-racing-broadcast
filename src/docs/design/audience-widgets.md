# Audience counters and goals

Issue #932 extends the scene builders delivered in #930, #931 and #935.

| Requirement | Production path | Positive behavior test | Error behavior test | Runtime proof |
| --- | --- | --- | --- | --- |
| Multiple shared counters/goals | Profile-scoped audience.json, UI CRUD | Stable IDs, edits affect every placed scene | Invalid finite/range/length/type data leaves disk unchanged | Add two differently named widgets in actual builder |
| Per-scene placement and styles | Dynamic slots in HUD/Intermission/Split model | Same ID has independent coordinates/presentation | Solo Split rejected; stale scene action has no effect | Move one placement, reload all three pages |
| Plain number/target/bar | Shared pure normalization and live renderer | Raw exceeded target preserved, fill clamped; valid zero renders | Unknown/missing/stale never coerced to zero | Actual browser text and computed fill width |
| Shared manual value edits | Local UI API writes, relay reads cached local data | Every live page updates without reload | Partial/invalid config does not replace last-good state | Open two pages, edit value via real control |
| Explicit goal reset and baseline | Persisted fields, explicit operator action | Restart/reload preserves baseline, target remains fixed | No baseline shows unavailable session gain | Actual reset/baseline click, source reload |
| Formatting and text safety | Renderer textContent; bounded formatter | Label/unit/precision applied consistently | Script/markup label rendered inertly; non-finite rejected | Browser has no injected element/event |
| Offline and no-widget defaults | Manual reader, empty definitions | No account/Sheet/OBS needed; empty overlay unchanged | Optional automatic source unavailable only affects itself | Synthetic relay without outbound network |
| Automatic value contract | Shared provider adapter seam, fake clock | Last-good held within documented interval | Expired value hides affected widget; operator reason remains | Advance fake clock on shipped renderer fixture |
| Portability and Look separation | Config outside overlay/, bundle and backup paths | Profile bundle carries definitions; Look restores placement only | Cross-profile and credentials never leak | Two isolated profiles + export/import + Look roundtrip |
| Preview isolation | Builder sample source only | Clearly marked sample renders offline | Sample edit never writes manual/provider live value | Compare disk and relay value before/after preview edit |
| Existing workflows | Current HUD/Intermission/Split data/render paths | Existing labels/timer/chat/frames untouched | Unsupported widget IDs/props dropped, no OBS controls | Existing browser and scene regression harness |
| Docs and discoverability | Builder help/wiki/screenshots | User can distinguish remove placement vs delete shared definition | Shared delete warning identifies all affected scenes | Screenshot actual add/edit/place panel |

Decisions recorded before implementation:
- One local writer owns shared config edits; relay reads atomic snapshots and polls providers independently of browser request count.
- Keep definitions/manual values/baselines outside overlay/ so restoring a Look cannot rewind a running challenge. Bundle includes definitions; machine credentials/caches excluded.
- Explicit total vs session mode and capture baseline action; never infer event counts from net totals or rounded providers.
- Label and unit are text, stable generated ID is the only API/slot identity.
- Presentation and show-number/show-percent belong to scene slot config. Removing one placement retains the shared goal.
- No implicit rendering of all definitions on every scene. A live page renders only IDs explicitly placed in that scene's saved model.

## Data and activation boundaries

Definitions, manual values and explicit baselines live in the profile's
`audience.json`, outside `overlay/`. Profile bundles carry them; restoring a Look
restores placement/style without rewinding values or resurrecting a deleted goal.
A manual total reset sets its value to the configured start; a net reset captures
the current raw value as its baseline. Targets never change automatically.

Scene layouts contain placements/styles only. Save persists a draft; existing
Apply/browser refresh activates the saved markup and stylesheet. Already placed
widgets receive shared value/label/target changes through one local data request
per page without reload. The renderer updates existing nodes and never creates
new placements merely because another scene was saved.

Automatic values use a server-side cache, with a 120-second last-good hold for
transient failures. Unknown, unavailable and stale are separate from zero. A
relative client deadline also expires automatic widgets when the relay becomes
unreachable; manual static values retain their last known value. Metric source
precision and baseline identity remain explicit. Platform fetchers/authentication
are separate delivery issues, while the shared contract can be tested offline.

All labels/units are text. Dynamic IDs are generated and validated separately.
Preview values belong to the editor session and cannot write live manual values,
baselines or provider data. Shared edits and scene placement actions are distinct.

Audience reads capture the profile once and return that identity with the data.
Slot markup and metadata share one audience snapshot. The builder retries a
profile mismatch once during loading and rejects a mismatched post-save reload
before replacing the current editor state.

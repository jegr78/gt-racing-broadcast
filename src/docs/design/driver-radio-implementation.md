# Race Engineer driver radio implementation plan

Implement the confirmed [driver radio scope](driver-radio.md) in six dependent
deliveries. These are planned changes, not completed functionality. Pace and tyre
assessment belong to the first version alongside fuel warnings.

Follow current repository contribution and build rules. Runtime code and reusable
browser assets belong under `src/`; do not import maintainer scripts into shipped
code. Keep docs and UI copy in English. Use synthetic fixtures and fake services
for repeatable verification. No paid AI or speech calls in ordinary tests.

## 1. Define radio access and live contracts

- [ ] Extend profile access storage and Console policy with the narrow driver
  radio opt-in and authenticated assignment, without broadening Engineer reads.
- [ ] Define revisioned optional race setup, service confirmation, preset enum,
  message evidence and session/context invalidation contracts.
- [ ] Specify Console sign-in handling for the direct Director Panel; preserve
  existing panel controls when radio access is unavailable.
- [ ] Add signed HTTP cases for driver-only access, Director without assignment,
  Engineer without radio assignment, producer administration, disabled profiles,
  revocation, cross-profile reads and forged mutations.

Likely integration points: `src/scripts/race_engineer_access.py`,
`src/scripts/console_policy.py`, and `src/relay/racecast-feeds.py`. Keep the radio
projection distinct from the existing analysis API.

Completion evidence: authenticated radio-only access can read allowed live facts
and update allowed setup fields; existing Engineer and producer boundaries hold.

## 2. Implement deterministic assessments

- [ ] Add a pure radio assessment module consuming existing live instruments,
  confirmed lap summaries and compatible declared context.
- [ ] Compute fuel range and reserve-aware finish estimates with explicit unknown
  states. Preserve the existing session average; expose any new rolling estimate
  with its own sample basis.
- [ ] Implement robust recent-versus-baseline pace evaluation and conditional
  tyre advice. Use existing eligibility and compatibility helpers rather than
  duplicating their interpretation.
- [ ] Choose and document initial window sizes, minimum evidence, noise margin
  and reset behavior using replay fixtures. Do not tune against a single drive.
- [ ] Cover stable and degrading pace, outliers, missing traffic knowledge,
  incomplete captures, service, compound and strategy changes, unknown setup,
  refuelling, race completion and timed-race final-lap assumptions.

Likely sources: `src/scripts/race_engineer_live.py`, `src/scripts/gt7_context.py`
and existing lap compatibility helpers. A new `src/scripts/driver_radio.py` can
own pure assessment and message formatting without modifying existing desk math.

Completion evidence: all four deterministic preset answers and grounded automatic
fuel, pace and tyre assessments are available without network services.

## 3. Add the shared message service

- [ ] Add one relay-owned bounded worker and message history, triggered by live
  revisions and lap completion rather than each browser's polling cycle.
- [ ] Implement threshold-crossing warnings, rearming, configurable fuel limits,
  severity, deduplication and cooldown for meaningful pace changes.
- [ ] Publish versioned radio reads, fixed preset requests, setup updates and
  service confirmation routes with current authorization on every operation.
- [ ] Bind all work to profile, relay generation, session and context revision;
  invalidate work on reset, revocation and context corrections.
- [ ] Verify multiple clients share work, bounds remain enforced, telemetry
  ingestion is never blocked, and reconnect cannot replay expired warnings.

Completion evidence: real ephemeral HTTP tests exercise live transitions,
concurrent clients, access changes and delayed worker completion.

## 4. Integrate the English voice and optional AI

- [ ] Add a shipped Google TTS adapter through `http_util`, retaining
  `en-US-Studio-Q` and the established server-side credential configuration.
  Reuse the tutorial request design, not the maintainer tool as a runtime import.
- [ ] Bound synthesis work, response size and cache storage. Publish text first;
  reject obsolete completions before exposing playable audio.
- [ ] Add exclusive playback claims with renewal and safe transfer. Define expiry
  so an old client stops before a replacement can start, across origins/devices.
- [ ] Adapt existing AI agent configuration for compact optional explanations
  with independent live deadlines, bounded concurrency and a validated schema.
- [ ] Preserve deterministic facts and recommendations on AI timeout, invalid
  output, service failure and missing credentials. Keep secret values out of
  browser responses and diagnostics.
- [ ] Measure configured Studio-Q cold/warm latency using non-sensitive sample
  messages. Use the result to set live deadlines; record any voice-latency limit.

Completion evidence: fake services cover failures and late results; a separate
configured smoke check confirms the selected voice and practical response time.

## 5. Build the two browser views

- [ ] Create a shared radio UI module for presets, messages, audio state and
  claim renewal so desk and panel use the same behavior.
- [ ] Add the compact Solo POV radio card to `src/director/director-panel.html`.
- [ ] Add the authenticated driver desk, launcher entry, bounded history,
  evidence details, optional race setup and tyre-change confirmation.
- [ ] Add explicit audio activation, volume and mute. Reflect blocked playback,
  synthesis failure and access loss without hiding the text answer.
- [ ] Stop queued speech on expiry, pause, profile/session/context changes,
  claim loss and revocation. Prioritize urgent fuel over routine speech.
- [ ] Verify actual UI behavior with synthetic browser fixtures, including two
  views, ownership transfer, multiple origins, reconnect and delayed audio.

Completion evidence: the four presets, automatic fuel and pace/tyre advice work
through production UI code. Driver-only users cannot reach archive or producer
operations. No old or duplicate announcement survives the lifecycle cases.

## 6. Package and document the complete first version

- [ ] Register new runtime modules and browser files in source, flat-package and
  frozen-binary discovery and packaging as applicable.
- [ ] Add wiki guidance for driver grants, setup, conditional tyre advice, Google
  TTS configuration, text fallback and deliberate OBS audio capture.
- [ ] Refresh screenshots for every changed Director and Console UI surface in
  the same delivery, using synthetic data and the current screenshot workflow.
- [ ] Run focused assessment, HTTP, access and browser tests as changes land.
  Complete repository lint, full test suite, relay regression and distributable
  build verification for the integrated feature.
- [ ] Review the release against every acceptance condition in the spec. Update
  documentation with actual limits and chosen engineering defaults.

This delivery completes the entire agreed first version. Interim text-only or
fuel-only stages are implementation milestones, not a reduced final scope.

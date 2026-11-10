# Race Engineer driver radio

Confirmed product scope, 2026-10-23. Implementation has not started.

The driver can request live race information through preset buttons and receive
short spoken and written answers. The Engineer also announces fuel risks and
meaningful pace or tyre concerns. The first version includes tyre assessment,
with explicit uncertainty about the cause of slower laps.

## Driver experience

One shared radio function appears in a compact Solo POV Director Panel card and
a dedicated driver desk. Both show the same session messages and request state.
The card contains the preset buttons, latest answer and audio controls. The desk
adds the bounded message history, assessment evidence and optional race setup.
Only a browser view explicitly activated for audio plays announcements.

| Preset | Required answer |
| --- | --- |
| Fuel status | Current fuel, measured consumption, estimated remaining laps, and finish feasibility when known |
| Pace | Last eligible lap, eligible best, and a comparison of recent comparable laps |
| Tyre temperatures | Four measured temperatures, declared compound and confirmed tyre age when available |
| Short status | A brief summary of fuel, pace and any supported tyre concern |

Answers and spoken messages default to English. Interface language is independent.
The default voice is Google Cloud Text-to-Speech `en-US-Studio-Q`, the voice
previously selected for tutorial videos. Browser output goes to the PC audio
route. The driver can deliberately capture that route in a Solo POV broadcast;
the feature does not automatically change OBS sources or PlayStation audio.

Optional race setup contains race length in laps or time, starting compound and
desired fuel reserve. A driver can confirm a tyre change. Missing setup never
blocks measured status answers, but limits strategic conclusions. A confirmed
change creates a new tyre stint; inferred service alone cannot confirm it.

## Existing foundation

- `src/scripts/race_engineer_live.py` provides bounded live state, completed lap
  summaries, eligibility, fuel averaging and a five-second freshness boundary.
- `src/scripts/gt7_context.py` distinguishes measured data, declared context,
  confirmed service and comparison compatibility.
- `src/scripts/race_engineer_api.py` and the relay expose protected Engineer
  projections. Existing routes remain read-only and retain their current rights.
- `src/director/director-panel.html` already adapts to Solo POV. Its program
  audio monitor is a playback-control example, not a speech service.
- `src/scripts/ai_agents.py` supports configured Codex and Claude CLI analysis.
  The current long-running report workflow is not a live response mechanism.
- `tools/build-walkthrough-videos.py` contains the existing Google synthesis
  integration and voice selection. Maintainer tooling is not shipped runtime.

## Fuel warnings

Consumption and range remain deterministic. Missing, zero or incompatible
consumption is unavailable, never a fabricated estimate. Use current stint and
strategy compatibility when selecting consumption samples; report sample count
and distinguish an initial estimate from a stable one. Do not silently replace
the existing desk's session average with a different statistic.

Configurable warning defaults are approximately two laps remaining and an urgent
warning at one lap. Distinguish estimated physical range from range after the
configured reserve. Account for remaining race distance when it is known.
A timed-race finish is an estimate with an explicit final-lap assumption, not an
exact known distance. Without enough finish context, report range and preserve
the range warning rather than claiming the car will finish.

Use threshold crossings and rearm after meaningful refuelling or session reset.
A persistent unchanged risk must not generate a warning on every packet. Urgent
fuel facts do not wait for AI or speech generation.

## Pace and tyre assessment

Evaluate completed, confirmed, comparable laps using existing pace eligibility
and context rules. Keep car, track, session, compound, confirmed service and known
strategy changes separate. Exclude known pit, warmup, invalid and incompatible
laps. Missing traffic information cannot be treated as proof of a clear lap.

The current live delta arrow is not a multi-lap trend. Add a separate assessment
that compares robust recent pace with an earlier compatible baseline, publishes
the lap counts and observed change, and identifies context gaps. Sample windows,
noise thresholds and cooldown values are engineering defaults to validate with
replay fixtures during implementation, not measured tyre-wear thresholds.

The first version must produce useful pace and tyre assessments without AI.
When insufficient comparable laps exist, return an explicit unavailable or
provisional assessment. Unknown compound or service history reduces confidence
in tyre attribution rather than preventing an observed pace comparison.

Tyre wear is not directly measured. Temperature alone cannot establish ideal
temperature or wear without a supported target. Slower pace can justify a
conditional recommendation, such as considering tyres at the next stop, but
cannot justify an unconditional tyre-based box command.

Example with illustrative values: "Your recent comparable laps are 0.8 seconds
slower. Tyres may be contributing. Consider changing them at the next stop."
The detail view states which laps and context support the assessment. Never
describe traffic, weather, penalties or wear percentage as observed when absent.

## Message and playback lifecycle

A relay-owned radio service creates one bounded stream of structured messages
per active profile and live session. Browser count does not multiply assessment
work, warnings, AI calls or synthesis. Each message identifies its source facts,
generation, session, context revision, creation time, expiry, severity and
whether it is a requested answer or an automatic announcement.

Facts and text publish first. Optional AI explanation and speech are asynchronous
attachments to the same message, not duplicate chat posts. Reject late results
when the message expires or its profile, session, context or authorization has
changed. Invalidation must also reach browser playback queues and downloads.

Pace and tyre announcements occur after lap completion and only for a meaningful
new concern. With lap updates on, a race position change within a lap is called
as the count gained or lost since the last named position plus the new position.
It must hold for 3 s, calls keep 15 s apart, and a pending lap update, practice,
qualifying and a finished race suppress it. The call has its own preset, so it
never supersedes a queued lap update or a driver's position question.
Urgent fuel messages take priority over routine speech. A bounded
queue discards superseded status instead of reading a backlog. Pause, menus,
loading and stale data suppress automatic announcements. A requested answer can
explain that live information is unavailable. Reconnection never reads old alerts.

Audio activation requires an explicit browser gesture. An exclusive, renewable
playback claim shared across desk and panel prevents simultaneous announcements,
including views on different origins or devices. Transfer stops the old owner;
loss of renewal stops playback before another owner is granted. Text remains
available everywhere authorized. Closing a view does not stop telemetry capture.

Volume, mute and playback availability are visible. If synthesis or playback
fails, retain the text and show the failure. Do not silently substitute a
different voice. Retrying speech must recheck freshness and ownership.

## AI and speech services

Rules compute all numerical facts, fuel warnings, pace evidence and baseline tyre
assessment. Optional configured AI can explain those facts and form cautious
recommendations within a bounded response schema. It cannot invent measurements,
change thresholds, execute controls or delay core answers. Validate numerical
claims against supplied facts before publication; discard unsupported output.

Reuse suitable configured agent adapters with a separate live work budget and
queue. The report timeout and report job lifecycle do not define live behavior.
No new mandatory AI provider is part of this scope. AI disabled, busy, unavailable
or late leaves the deterministic answer intact.

Google synthesis runs server-side through shared HTTP utilities. Secrets remain
server-side. Cache reusable phrases and identical utterances in bounded storage;
invalidate pending playback independently of cached bytes. Measure cold and warm
latency before choosing deadlines. Google TTS remains optional to operation:
without configuration or network access, the radio works as text.

External service use is explicitly configured. Send only the compact facts needed
for the explanation or the text needed for synthesis. Do not send raw recordings,
private paths, credentials or unrelated crew chat. Service availability and
configuration are separate from a driver's permission to use the radio.

## Access and integration

Add a profile-scoped driver-radio opt-in and explicit authenticated driver access,
managed by the producer. This narrowly permits presets, current radio messages,
audio and the driver's race setup and service confirmations. Director membership
alone does not grant radio or full Engineer access. Existing Engineer archive
grants and producer controls remain independent.

The authenticated Console Director Panel and the direct relay panel have different
entry contracts. A direct-panel visit must establish a supported Console identity
before accessing protected radio functions. Do not expose radio endpoints as
tokenless routes to make embedding convenient. A denied card can show a sign-in
or access-required state without affecting existing Director controls.

Validate current identity, assignment, profile opt-in and session binding on each
read, mutation, playback claim and audio fetch. Mutations use the existing
same-origin and request-forgery protections. Revoke open views, pending work and
playback when access changes. Setup writes have their own narrow allowlist and
revision checks; they cannot edit producer telemetry context generally.

Route names and storage fields are implementation details. The service contract
needs snapshot and incremental message reads, fixed-enum preset requests,
revisioned setup and confirmed service writes, audio activation and renewal, and
authorized message audio retrieval. It accepts no arbitrary prompts or paths.

## Acceptance

Both views answer all four presets, share messages and enforce one audio owner.
Fuel warning crossings and finish-aware reserve handling work without AI.
Replay cases demonstrate stable pace, genuine slowdown, noisy laps, warmup,
service and strategy transitions, missing context and insufficient samples.
Tyre advice is conditional and supported by published evidence.

Pause, stale data, reconnect, session or profile changes, role revocation and late
AI/TTS completion cannot play an obsolete announcement. Unconfigured services
retain usable text. Signed HTTP tests prove the Director, driver, Engineer and
producer access boundaries. Production browser fixtures verify panel and desk
controls, queue behavior and ownership transfer. Live voice latency requires a
separate configured smoke check; ordinary CI does not call paid services.

## References

- [Existing Engineer access](race-engineer-access.md)
- [Existing Engineer API](race-engineer-api.md)
- [Existing live collection](race-engineer-live.md)
- [Implementation plan](driver-radio-implementation.md)
- [GTRE inspiration](https://www.skepller.dev/gtre/)
- [Google voice catalog](https://cloud.google.com/text-to-speech/docs/voices)


# Deterministic driver radio assessments

Epic #969, delivery #971. This module supplies the four preset answers and facts
for the shared service in #972. It does not create a browser-owned poll worker.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Four useful English answers without services | driver_radio_assessment.assess | fuel/pace/tyres/status from live snapshot | missing, paused, stale data return explicit unavailable | real telemetry replay through production assessment |
| Separate compatible recent consumption | bounded cohort, shared context compatibility | median of last five completed comparable laps | session/car/track/strategy/service/warmup/pit exclusion | cohort IDs and sample counts in result |
| Reserve and finish-aware range | deterministic fuel projection | physical/race-usable range, lap/time finish | zero/nonfinite consumption, unknown progress/start, finished race | synthetic partial-lap and timed-race snapshots |
| Robust multi-lap pace | three-lap baseline and recent medians | repeated slowdown and stable pace | isolated outlier/noise, inadequate sample, context reset | replay-case evidence IDs and threshold |
| Conditional tyre advice only | declared service/compound plus measured temperature | four corners and conditional advice | absent wear/traffic/targets never invented | actual formatted English answers |
| Configurable warning thresholds | validated finite limits | warning at two laps, urgent at one | invalid order/types/ranges rejected | deterministic assessment flags consumed by #972 |

## Cohorts and defaults

Keep the Engineer's session fuel average unchanged. Radio uses at most the newest
five completed comparable laps after the latest known service or context barrier.
A single sample is provisional; three or more support the normal estimate.
Known pit, first, warmup, partial and invalid laps cannot supply comparable pace
or radio consumption. An unsuitable lap is excluded, never relabelled clear.

Walk backwards from the current lap, stopping at a contradictory car, track,
compound, declared strategy/settings, service boundary or confirmed radio tyre
change. Use gt7_context.compatible in both directions so missing declarations do
not bridge a known contradiction. Unknown context is published as uncertainty.
A radio service confirmation bounds subsequent eligible laps by its server-time
marker and current lap identity. The confirmation lap and next warmup lap are
excluded; this is a conservative engineering default, separate from producer
context. Inferred service ends a cohort even without a confirmed tyre decision.

Pace compares three earlier comparable laps with the latest three, using medians.
The slowdown threshold is the maximum of 0.5 seconds, 0.5 percent of baseline and
three baseline median absolute deviations. At least two of the three recent laps
must exceed the baseline by that threshold. One slower lap cannot create tyre
advice. These are configurable engineering defaults, validated with stable,
trending, isolated-outlier and noisy synthetic cases; they are not wear targets.
The evidence lists exact IDs, counts, medians and threshold. Both missing traffic
knowledge and unknown tyre/context stay unknown. A slowdown is observed; tyres
are one possible cause. Never issue an unconditional box command from pace.

## Range and finish

Physical range is measured current fuel divided by recent median consumption;
usable range subtracts the explicitly configured reserve, clamped at zero.
Default warning and urgent levels are two and one usable laps, configurable.
A known lap-race finish can suppress a range warning only when the current lap
number/progress and stable consumption support finishing with the reserve.
Estimate current progress against the median length of completed compatible laps,
not the current partial trace's length. Missing line observation or full-lap
length prevents a finish claim. Race completion yields an explicit complete state.

Timed races require a completely observed sequence from lap one plus current
lap elapsed time. Estimate remaining time using comparable median lap time and
publish the conservative assumption of one additional lap after timer expiry.
This estimate does not suppress a low-range warning, since actual final-lap rules
and current pace can differ. Missing start, times or setup leaves finish unknown.
Missing setup does not block measured fuel/temperature/pace answers.

Only receipt.state=live supports current measurements or automatic announcements.
Paused/loading/menu/stale/delayed/replay snapshots retain explanatory text and
historical evidence but do not present old instruments as current or emit alerts.
No external service, AI inference, arbitrary target temperature or wear percentage
is needed to produce any preset answer.

## Review and verification

Independent review reproduced stale session declarations suppressing a current
fuel warning. Nonempty declarations now require exact generation, session and
context binding; otherwise they are discarded with an explicit uncertainty.
Regression cases cover all three mismatches and absent binding. Current service
boundaries and authoritative zero fuel burn also reject old estimates. Extremely
small consumption and nonfinite derived ratios stay unavailable. Timed-race
continuity checks use bounded enumeration, never an unbounded range allocation.

Twenty protections were falsified individually and restored after the intended
assertion failed: declaration binding, live receipt, replay silence, car/track
identity, current/historical service boundaries, shared context compatibility,
completed laps, pit/warmup exclusion, sample minimum, repeated slowdown, observed
line, completed reference length, known timed start, reserve subtraction, stable
finish estimate, urgent/warning crossings, radio service boundary and limit order.
A real production TelemetryStore replay supplies positive fuel/temperature facts;
no browser, recording writer or external provider is needed by assessment.

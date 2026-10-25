# Live driver coach

Implementation authorized for epic #977 on 2026-10-25. The native GitHub children
are the delivery units. The existing completed-session review remains separate.
No paid calls, microphone capture or model training are required for acceptance.

## Delivery order and evidence

| Issue | Production contract | Positive test | Failure-path test | Runtime proof |
| --- | --- | --- | --- | --- |
| #978 | Calibration and frozen personal/reference evidence | Two comparable laps permit provisional feedback; three establish readiness | Partial/warmup/pit/invalid laps, changes and revoked reference grants | Real synthetic packets through the live Store and calibration |
| #979 | Deterministic technique and consistency findings | Measured inputs and a downstream check support one experiment | Missing channels, incompatible geometry and unsupported ideal targets | Synthetic connected passages and production derived metrics |
| #980 | Objective, sustained focus and repeated attempts | A recurring focus survives attempts; repeated gains are distinguished from outliers | Unsuitable laps, context changes, skip/keep and regression | Multi-lap deterministic coaching sequence |
| #981 | Relay-owned coach and shared radio scheduling | Completed laps publish text before optional AI; one native owner | Urgency, deadlines, cancellation, stale replies and bounded queues | Owned relay/service replay and controlled provider processes |
| #982 | Scoped driver controls and voice requests | Start/stop, objective, focus and evidence in shared views | Grants, revisions, stale requests, unsupported voice and DOM privacy | Signed HTTP and real browser acceptance |
| #983 | Private compact debrief and continuity | Per-driver/car/track summaries suggest a revalidated next focus | Profile/identity mismatch, retention, deletion and interrupted sessions | Restart and authenticated history lifecycle |
| #984 | Integrated acceptance and documentation | End-to-end coaching through all delivered contracts | Missing evidence, context/reference revocation and provider failures | Replay, native-worker, browser, package and actual OS checks |

Every issue receives code review and an independent security pass before the
committed-head full test suite, lint and distributable build. Green PR checks,
verified merge, main/CodeQL verification and tracker reconciliation follow.

## Calibration contract, #978

Training is explicitly started for practice or time trial. It never starts merely
because telemetry is available. Balanced is the default objective; Consistency
and Pace use the same evidence eligibility. The trusted caller supplies the
profile/driver identity, separately from later HTTP authorization.

Only confirmed, complete, time-valid, comparison-eligible regular laps count.
Known service/warmup/pit laps are excluded. A duplicate or out-of-order lap cannot
advance calibration. Missing context remains unknown; no compound, strategy or
track definition is inferred from speed or lap time.

The first two suitable laps form the immutable starting baseline. Their actual
lap identities and individual traces are retained, never a synthetic best-sector
lap. A third suitable lap strengthens readiness without changing the original
progress denominator. The working reference is one actual personal lap. During
a focus it stays frozen; a better compatible personal candidate can replace it
after the focus ends. An explicitly selected earlier/shared reference remains
selected until cleared, revoked or invalidated.

Relevant session, car, track/layout, tyre/stint, strategy/settings, objective or
definition changes reset calibration and focus with a new epoch. Corrections to
already accepted measurements also invalidate the epoch. Compact invalidation
provenance retains the previous baseline identity; earlier facts are not edited.
Unsuitable laps do not count or promote references. Context reset occurs on the
next suitable lap or an explicit caller invalidation, so known current changes
must be forwarded by the live service before it publishes more advice.

Earlier/shared references need current authorization at selection and every
read. Authorization failures remove the reference and invalidate pending focus
work. Cross-session references need known matching car and track/layout identity;
unknown identity permits same-session personal calibration only. Known compound,
settings and strategy conflicts fail comparison. Unknown channels remain labelled
unknown, even when same-session personal comparison is usable.

Retain at most three calibration laps, a working reference and a best candidate,
with a bounded deduplication ledger and compact invalidation history. Reject
non-finite or oversized inputs before retaining them. Public snapshots contain
metadata, not full telemetry traces or recording filesystem paths. The caller
serializes coaching operations; no provider, process, network or persistence is
started by calibration itself.

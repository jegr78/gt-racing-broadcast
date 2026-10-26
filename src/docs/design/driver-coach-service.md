# Relay coach orchestration and radio, #981

One relay-owned coordinator, one active owner at a time. No panels own workers.
Trusted server constructs owner profile + namespace-local or namespace-console
subject; no incoming owner/subject fields. Local and Console identities differ.
Permissions/HTTP/UI/voice vocabulary are delivered in982, using these contracts.

| Requirement | Production path | Positive | Failure | Runtime |
|---|---|---|---|---|
| Explicit training and watermark | Coordinator start command | New full laps after captured current lap/session/time only | Old Store history/current partial lap cannot calibrate | Real Store replay |
| Calibration+assessment+focus | Single worker-owned training model | Two-lap hint, third readiness, repeated focus progress | Unsuitable/corrected/context/ref revoked resets or preserves as specified | Multi-lap replay |
| Responsive controls | Short state lock, generation cancellation, bounded command queue | Start/stop/focus change return without calculation/provider wait | Slow calculation loses publication race to Stop/revoke | Blocking calculation fixture |
| Fresh publication | Recheck owner grant, generation/context/definition/epoch before publish | Current frozen evidence produces text | Stale/unavailable/replay/newsession cannot publish old text/audio/AI | Events and source mutation fixtures |
| Deterministic baseline | Publish immediately after suitable completed lap | Grounded observation/action/check without AI | Missing geometry/provider leaves general text | Text-only replay |
| Shared native owner | Radio Service private coach message lane | Same Enricher+Supervisor+oneTicket | Urgent Engineer cancels/deprioritizes coach; no stale backlog | Existing native fake-process fixture |
| Private scope | Internal coach owner + history filter | Own Console sees own coach text; native explicitly started output | Other assigned drivers see no private coach messages | Service and signedHTTP982 |
| Optional AI consent | Volatile explicit allow_ai AND profile agent setting | One bounded current explanation | Default off, stale/invalid/late result never changes grounded facts | Controlled provider fixture, zero paid calls |
| Bounded scheduling | Single CPU worker, command/event budgets, bounded per-owner current state | Latest completed lap text, one pending speech | Delay/high workload expires voice rather than accumulating | Fake clock/deadline queue tests |

Start captures current generation/session/lap plus receipt wall time before the
worker starts. A just-completed old lap and the current partial lap are excluded.
A current-source context/definition change is forwarded to calibration before any
new publication, regardless of whether a new suitable lap exists. Metadata-only
revision/free note updates do not manufacture a technique/context change.

Original baseline and selected reference remain distinct. Start/focus/objective/
reference/owner/stop changes cancel all pending private coach speech and AI by a
fresh publication generation. Selected references need a fresh trusted callback
that verifies both source identity/context and archive grant on every read.

Calculation uses frozen detail rows and definition outside telemetry/view/access
locks. Start/control writes invalidate publication immediately; the single worker
applies bounded queued commands to its model. Recheck binding/grant after detail
lookup and calculation. Under overload, never replay a stale backlog of speech.

Speech uses the existing Radio Service/Ticket/Enricher/Supervisor lane, English
Studio-Q and selected input language. It is postponed during high pedal/steering
workload and expires at a short deadline. Urgent Engineer work has priority and
cancels active coach output. New lap/focus supersedes older coach output including
an active ticket. No competing recorder/player process or signal path is added.

AI receives only bounded current training facts/unknowns and the current exercise,
never private earlier-session history/raw traces/audio. Explicit allow_ai is per
training and defaultsFalse; profile provider selection remains mandatory. Model
explanations are validated attachments, cannot replace numeric facts/current focus,
and never gate the grounded text. Existing completed-session runs remain separate.

## Implemented scheduling and explanation constraints

One actor owns the mutable calibration/focus model. Controls invalidate a public
publication token immediately and occupy one bounded command slot. Stop preempts
that slot; other writes receive a busy response until the current command has been applied. Permission
is checked before enqueue and again before execution. Numerical calculation and
provider work never hold the state/view/access locks.

A 128-entry source-fact ledger pins the original baseline, personal reference,
best candidate, current cohort and contributors to the retained exercise/attempts.
Private radio callbacks revalidate those facts before playback or enrichment,
including while a calculation is blocked. Missing/corrected evidence fails closed.
Actual completion time bounds speech/AI to twelve seconds; only the latest lap may
speak. Delayed older laps retain deterministic text. Deferred speech leaves the
existing native microphone owner intact.

Every suitable lap review contains measured lap time and the retained exercise.
Observation, interpretation, action and check remain separate structured fields.
An unselected assessment suggestion never becomes a second action in the summary.

Optional AI requires both per-training consent and a configured isolated agent.
Only bounded current numeric facts, unknowns and the current exercise are sent.
The model selects the current experiment or a repeatability variant within that
same exercise. Explanation sentences are selected from server-owned provisional/conditional templates; numeric claims are
rendered from exact fact IDs/units. Arbitrary prose cannot introduce an additional
driving instruction through synonyms; invented targets, causes and learning claims
are rejected. Explanations cannot change the focus or replace the grounded text.
The shared radio enrichment lane keeps its existing deadline and rate limits;
failed/late output leaves the deterministic review available.

Revoking the active owner ends the training model and releases the single training
slot. A new owner receives a fresh calibration rather than the previous owner's
private state. A delayed obsolete authorization check cannot discard a model now
owned by a newer valid command. Only Stop may preempt an in-flight control.
Live detail must match its requested summary's lap identity, times and state
before it can mutate calibration.

A monotonic processed-lap cursor prevents old Store summaries from becoming new
work after deduplication eviction. Tracked measurement corrections are checked
separately, including pinned old baseline/reference contributors. A correction
invalidates the old model with provenance and captures a new partial-lap watermark.
Provisional-to-confirmed publication is awaited before first admission; it is not
mistaken for a correction. Ready bounded detail is required for technique scoring.

Reference grant checks and snapshots run outside the control-state lock. An
inactive calibration, including its session-budget stop, cannot publish new work.
The current owner can read the explicit per-training AI-consent flag.

Detail lookups rotate with a four-call per-step budget. Evicted/unavailable laps
are skipped with a compact lap/state status; pending rows are retried without
blocking newer ready laps. Once a newer lap is processed, older delayed detail
cannot roll the review backward or manufacture a backlog of spoken hints.

Stop signals an active coach ticket before attempting the radio lock and never
waits for a radio reader's grant I/O. Deferred queue cleanup uses the invalidated
training token; a grant result is checked again after I/O before it can authorize
any old message. The worker still serializes model transitions.

While coach audio is playing, the native owner permits only one outstanding
radio read. It polls cancellation while that read waits for grant I/O and closes
its own player before the read returns. The read helper never owns microphone
or player processes; cancellation cannot release a process slot before cleanup.

The native owner also watches the ticket's captured monotonic deadline during
a blocked read. A grant check cannot extend already playing speech beyond expiry.

Provider subprocess polling uses only cached cancellation, message identity and
deadline state. It never enters source, settings or reference-grant I/O. Every
published coach message retains its own cancellation event; Stop signals it before
attempting the radio metadata lock. A later publication cannot reset an old
provider's event. Fresh authorization remains mandatory before work admission and
before any output attachment. Already expired work skips external guards.

The current actor review identifies its exact radio message. Its validated AI
explanation is projected only when message, owner, training epoch and radio epoch
still match. The desk displays this attachment separately as AI interpretation;
a new lap, changed exercise or identity cannot inherit the previous explanation.

# Driver coaching controls and native vocabulary

Training remains one relay-owned actor. The producer controls local training in
Solo controls; explicitly assigned drivers control their own authenticated desk.
The compact Solo panel shows current state and links to detailed evidence. None
of these views starts training or owns native audio merely by opening.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
|---|---|---|---|---|
| Explicit start/stop/objective | Exact coach subroute and actor optimistic revision | Start captures fresh watermark; objective updates and Stop cancels output | Stale revision, wrong profile, forged owner/trace/provider/options rejected | Signed HTTP and browser actions |
| Calibration and reference | Sanitized actor snapshot + bounded reference choices | Calibration count, original baseline and working reference remain distinct | Missing live data/detail, revoked archive grant, incompatible row | Synthetic production lap data |
| One retained focus | Shared driver component | One exercise, attempt result, structured review and current evidence | Busy other owner discloses no focus/history; revoked user clears private DOM | Browser desktop/tablet/phone |
| Driver adjustments | Skill/skip/keep exact commands | Actor applies chosen skill or explicit skip/retention | Unsupported skill, pending/busy conflict, stale epoch/revision | HTTP + retained-focus replay |
| Narrow rights | Existing driver assignment + verified subject namespace | Assigned driver and trusted local producer can control their own training | Director/Engineer/archive permissions alone never imply driver rights; no owner impersonation | Role matrix HTTP |
| Optional selected reference | Server-only lap resolution + current source/grant callback | Own usable live lap or separately authorized shared archive | Client raw rows/paths forbidden; changed raw/context/grant cannot remain frozen-valid | Grant/context mutation HTTP replay |
| Native voice without browser | Existing Supervisor handles bounded coaching vocabulary | Exact English/German commands, status/focus/evidence question; grounded English reply on same Ticket lane | Ambiguous/unsupported coaching utterance clarifies without writes; native cannot control Console owner | Actual Supervisor fake worker/player |
| Consent | Explicit training allow_ai checkbox plus existing isolated profile agent | Actor reports the consent it actually accepted | Default off; cannot select/enable provider through training request | Browser + service state |
| Current asynchronous state | Separate generation/revision per profile/owner/component | New replies update current state, unsaved inputs retained | Older GET/POST reply after revoke/profile switch cannot revive private state or overwrite newer edits | Browser delayed responses |
| Shared radio output | Existing radio owner, English Studio-Q, selected input language | Coach commands/questions and lap feedback use same native capture/playback lifecycle | Urgent Engineer output first, expired/cancelled coach cannot revive | Native and radio regression suite |

Console coaching requires an explicitly assigned DRIVER_RADIO identity. Its
subject is derived from the verified token; it cannot be supplied in JSON. An
Engineer role or access to a shared archive does not grant coaching controls.
Selecting an archive additionally requires the existing Engineer/reference
permission; coaching never expands the archive grant. Local producer routes use
existing loopback, Host, Origin, Referer and Fetch Metadata checks, fixed local
relay target, expected profile and optimistic actor revision.

POST accepts exactly expected/action/options. Reference options accept a lap
selector, resolved and frozen by trusted server code, never a raw lap or path.
Permission/source identity is rechecked after a delayed read and before reply.
Controls never hold an access/view lock while reading an archive or calculating.

English/German wake-gated vocabulary includes explicit start/stop, objective,
skill and skip/keep, plus status/current focus/evidence questions. Matching is
bounded and deterministic. Unknown or conflicting coaching requests return a
short clarification; they never guess an action or create a new technique.
Native coaching always uses local:producer and cannot impersonate a Console
owner. Ordinary telemetry questions retain the existing radio path.

The UI distinguishes inactive, waiting for live data, calibrating, provisional,
ready, pending, paused, detail unavailable and another owner's busy state. The
passage label explicitly identifies reviewed versus provisional geometry.
Evidence uses existing measured IDs/values and text nodes, without raw trace
or private earlier-session history. Detailed debrief/history follows in #983.

Input consent and the captured native frame are checked when a spoken command
is admitted and applied. Ongoing local training uses the producer's profile
grant. Pausing listening alone does not stop calibration. Speech and spoken
answers keep the captured native binding and current training provenance.

Stop is preemptive. It requires the current owner and permission plus a valid
revision value, but accepts an older display revision so lap updates or blocked
reference reads cannot prevent stopping. Other writes retain exact revision
checks. Stop also remains available after the ordinary control rate budget.

Replies include the coordinator lifetime generation. Views reject older revisions
within that lifetime and retire earlier generations after a relay restart. A
fresh whoami response verifies the current Console identity before any delayed
coaching response is rendered. Changing profile/identity clears private DOM and
outstanding responses cannot restore the previous driver's exercise.

A skill may be selected during calibration. The preference is retained until
comparable evidence supports that skill; an unrelated fallback exercise does
not silently replace the constraint. Console owner identifiers preserve the
complete existing driver identity plus the server namespace.
